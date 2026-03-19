"""
Transformer classes for the Styx transpiler.
"""

import libcst as cst
import libcst.matchers as m


class ReturnHandlerTransformer(cst.CSTTransformer):
    """
    Finds 'return' statements and wraps them with the reply_to stack logic.
    Also injects 'ctx.put(state)' immediately before the logic.
    """

    def __init__(self):
        super().__init__()
        self.state_dirty_stack = [False]
        self.state_aliases = set()  # variables assigned from state[...]

    def _mark_dirty(self):
        self.state_dirty_stack[-1] = True

    def _is_graph_terminal(self, node: cst.CSTNode | None) -> bool:
        """
        Recursively checks if a node guarantees an exit (return, raise, or async dispatch).
        """
        if node is None:
            return False

        if isinstance(node, (cst.Return, cst.Raise, cst.Break, cst.Continue)):
            return True

        if isinstance(node, cst.SimpleStatementLine):
            # Check for ctx.call_remote_async(...) — after dispatch, function is done
            for child in node.body:
                if isinstance(child, cst.Expr) and isinstance(child.value, cst.Call):
                    func = child.value.func
                    if (
                        isinstance(func, cst.Attribute)
                        and isinstance(func.value, cst.Name)
                        and func.value.value == "ctx"
                        and func.attr.value == "call_remote_async"
                    ):
                        return True
            return any(self._is_graph_terminal(child) for child in node.body)

        if isinstance(node, cst.IndentedBlock):
            if not node.body:
                return False
            return self._is_graph_terminal(node.body[-1])

        if isinstance(node, cst.If):
            if node.orelse is None:
                return False

            body_terminal = self._is_graph_terminal(node.body)

            else_terminal = False
            if isinstance(node.orelse, cst.Else):
                else_terminal = self._is_graph_terminal(node.orelse.body)
            elif isinstance(node.orelse, cst.If):  # elif chain
                else_terminal = self._is_graph_terminal(node.orelse)

            return body_terminal and else_terminal

        return False

    def leave_Assign(self, _original_node, updated_node):
        for target in updated_node.targets:
            # Track state[...] = ... as dirty
            if m.matches(target.target, m.Subscript(value=m.Name("state"))):
                self._mark_dirty()
            # Track state aliases: var = state[...]
            if isinstance(target.target, cst.Name) and m.matches(
                updated_node.value, m.Subscript(value=m.Name("state"))
            ):
                self.state_aliases.add(target.target.value)
        return updated_node

    def leave_AugAssign(self, _original_node, updated_node):
        if m.matches(updated_node.target, m.Subscript(value=m.Name("state"))):
            self._mark_dirty()
        return updated_node

    def leave_Expr(self, _original_node, updated_node):
        """Detect method calls on state aliases (e.g., attr_1.append(...))."""
        if (
            isinstance(updated_node.value, cst.Call)
            and isinstance(updated_node.value.func, cst.Attribute)
            and isinstance(updated_node.value.func.value, cst.Name)
            and updated_node.value.func.value.value in self.state_aliases
        ):
            self._mark_dirty()
        return updated_node

    def _is_call_remote_async(self, node: cst.CSTNode) -> bool:
        """Check if a statement is a ctx.call_remote_async(...) call."""
        if isinstance(node, cst.SimpleStatementLine):
            for el in node.body:
                if isinstance(el, cst.Expr) and isinstance(el.value, cst.Call):
                    func = el.value.func
                    if (
                        isinstance(func, cst.Attribute)
                        and isinstance(func.value, cst.Name)
                        and func.value.value == "ctx"
                        and func.attr.value == "call_remote_async"
                    ):
                        return True
        return False

    def _has_reply_to_param(self, node: cst.FunctionDef) -> bool:
        """Check if a function has a reply_to parameter."""
        return any(param.name.value == "reply_to" for param in node.params.params)

    def leave_SimpleStatementLine(self, _original_node, updated_node):
        # Handle ctx.call_remote_async: prepend ctx.put(state) if dirty
        if self._is_call_remote_async(updated_node) and self.state_dirty_stack[-1]:
            put_state = cst.parse_statement("ctx.put(state)")
            return cst.FlattenSentinel([put_state, updated_node])

        return_node = None
        for node in updated_node.body:
            if isinstance(node, cst.Return):
                return_node = node
                break

        if not return_node:
            return updated_node

        ret_val = return_node.value if return_node.value else cst.Name("None")

        # Generate: return send_reply(ctx, reply_to, result)
        send_reply_call = cst.Call(
            func=cst.Name("send_reply"),
            args=[
                cst.Arg(value=cst.Name("ctx")),
                cst.Arg(value=cst.Name("reply_to")),
                cst.Arg(value=ret_val),
            ],
        )

        res_stmt = cst.SimpleStatementLine(body=[cst.Return(value=send_reply_call)])

        put_state = cst.parse_statement("ctx.put(state)")

        return cst.FlattenSentinel([put_state, res_stmt]) if self.state_dirty_stack[-1] else res_stmt

    def leave_FunctionDef(self, _original_node, updated_node):
        body_stmts = updated_node.body.body
        last_stmt = body_stmts[-1] if body_stmts else None

        if not self._is_graph_terminal(last_stmt):
            new_body = list(updated_node.body.body)
            # Add send_reply for functions with reply_to so the reply chain isn't lost
            if self._has_reply_to_param(updated_node):
                if self.state_dirty_stack[-1]:
                    new_body.append(cst.parse_statement("ctx.put(state)"))
                new_body.append(cst.parse_statement("return send_reply(ctx, reply_to, None)"))
            elif self.state_dirty_stack[-1]:
                # No reply_to but state dirty — just flush state
                new_body.append(cst.parse_statement("ctx.put(state)"))

            if new_body != list(updated_node.body.body):
                return updated_node.with_changes(body=updated_node.body.with_changes(body=new_body))

        return updated_node


class RemoteCallLinearizer(cst.CSTTransformer):
    """
    Linearizes remote calls within functions.
    """

    def __init__(self):
        super().__init__()
        self.call_counter = 0

    def leave_FunctionDef(self, _original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        """Process each function and linearize remote calls."""
        self.call_counter = 0

        linearizer = StatementLinearizer()
        new_body = updated_node.body.visit(linearizer)

        return updated_node.with_changes(body=new_body)


class StatementLinearizer(cst.CSTTransformer):
    """
    Linearize method calls within statements.
    """

    def __init__(self):
        super().__init__()
        self.counter = 1

    def leave_SimpleStatementLine(
        self, _original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.SimpleStatementLine]:
        """Process each statement and extract method calls."""
        new_statements = []

        for stmt in updated_node.body:
            extractor = CallExtractorAndReplacer(self.counter)
            new_stmt = stmt.visit(extractor)

            should_collapse = False
            last_extracted_var = None
            last_extracted_call = None

            if extractor.extracted_calls:
                last_extracted_var, last_extracted_call = extractor.extracted_calls[-1]

                if isinstance(new_stmt, cst.Expr) and isinstance(new_stmt.value, cst.Name):
                    if new_stmt.value.value == last_extracted_var:
                        should_collapse = True

                elif (
                    isinstance(new_stmt, cst.Assign)
                    and len(new_stmt.targets) == 1
                    and isinstance(new_stmt.value, cst.Name)
                    and new_stmt.value.value == last_extracted_var
                ):
                    should_collapse = True

            if should_collapse:
                extractor.extracted_calls.pop()
                new_stmt = new_stmt.with_changes(value=last_extracted_call)

            self.counter = extractor.counter

            for var_name, call in extractor.extracted_calls:
                assignment = cst.SimpleStatementLine(
                    body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
                )
                new_statements.append(assignment)

            new_statements.append(cst.SimpleStatementLine(body=[new_stmt]))

        return cst.FlattenSentinel(new_statements)

    def leave_If(self, _original_node: cst.If, updated_node: cst.If) -> cst.If | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle if statements specially."""
        new_statements = []

        extractor = CallExtractorAndReplacer(self.counter)
        new_test = updated_node.test.visit(extractor)
        self.counter = extractor.counter

        # If no calls were extracted, return unchanged to avoid
        # FlattenSentinel in positions that don't support it (e.g. elif)
        if not extractor.extracted_calls:
            return updated_node

        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_statements.append(assignment)

        new_if = updated_node.with_changes(test=new_test)
        new_statements.append(new_if)

        return cst.FlattenSentinel(new_statements)


class CallExtractorAndReplacer(cst.CSTTransformer):
    """
    Extract and replace method calls with variables.
    """

    def __init__(self, start_counter=1):
        super().__init__()
        self.extracted_calls: list[tuple[str, cst.BaseExpression]] = []
        self.counter = start_counter

    def leave_Call(self, _original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        if isinstance(updated_node.func, cst.Attribute):
            receiver = updated_node.func.value
            new_func = updated_node.func

            if not isinstance(receiver, cst.Name):
                receiver_var_name = f"attr_{self.counter}"
                self.counter += 1
                self.extracted_calls.append((receiver_var_name, receiver))

                new_func = new_func.with_changes(value=cst.Name(receiver_var_name))

            new_call = updated_node.with_changes(func=new_func)

            var_name = f"attr_{self.counter}"
            self.counter += 1

            self.extracted_calls.append((var_name, new_call))

            return cst.Name(var_name)

        return updated_node


class InitBodyTransformer(cst.CSTTransformer):
    """
    Transforms __init__ method body to extract state dictionary entries.
    """

    def __init__(self):
        super().__init__()
        self.state_dict_entries = []
        self.other_statements = []

    def leave_SimpleStatementLine(self, original_node, updated_node):
        for stmt in original_node.body:
            if isinstance(stmt, cst.AnnAssign):
                if m.matches(stmt.target, m.Attribute(value=m.Name("self"))):
                    key = stmt.target.attr.value
                    value = stmt.value

                    self.state_dict_entries.append(cst.DictElement(key=cst.SimpleString(f"'{key}'"), value=value))
                    return cst.RemoveFromParent()
            elif isinstance(stmt, cst.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0].target
                if m.matches(target, m.Attribute(value=m.Name("self"))):
                    key = target.attr.value
                    value = stmt.value

                    self.state_dict_entries.append(cst.DictElement(key=cst.SimpleString(f"'{key}'"), value=value))
                    return cst.RemoveFromParent()

        self.other_statements.append(updated_node)
        return updated_node


class StateAccessTransformer(cst.CSTTransformer):
    """
    Transforms:
    1. self.attribute -> state['attribute']
    2. self           -> ctx.key
    """

    def leave_Attribute(self, original_node, updated_node):
        # Handles self.attribute -> state['attribute']
        if m.matches(original_node, m.Attribute(value=m.Name("self"))):
            return cst.Subscript(
                value=cst.Name("state"),
                slice=[cst.SubscriptElement(slice=cst.Index(value=cst.SimpleString(f"'{original_node.attr.value}'")))],
            )
        return updated_node

    def leave_Name(self, original_node, updated_node):
        # Handles standalone 'self' -> 'ctx.key'
        if m.matches(original_node, m.Name("self")):
            return cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))
        return updated_node


class AnnotationNameReplacer(cst.CSTTransformer):
    def __init__(self, get_key_type_func):
        super().__init__()
        self.get_key_type = get_key_type_func

    def leave_Name(self, original_node, updated_node):
        replacement = self.get_key_type(original_node.value)
        if replacement:
            return updated_node.with_changes(value=replacement)
        return updated_node


class EntityTypeReplacer(cst.CSTTransformer):
    """
    Replaces entity type references in annotations with the key's type.
    e.g., `item: Item` -> `item: str`, `-> Item` -> `-> str`
    Also handles: `items: list[Item]` -> `items: list[str]`, `list[list[Item]]` -> `list[list[str]]`
    """

    def __init__(self, entity_keys: dict[str, str], entity_init_params: dict[str, dict[str, str]]):
        super().__init__()
        self.entity_keys = entity_keys
        self.entity_init_params = entity_init_params

    def _get_key_type(self, entity_name: str):
        """Resolve an entity name to its key's type string, or None."""
        key_field = self.entity_keys.get(entity_name)
        init_params = self.entity_init_params.get(entity_name)
        if key_field and init_params and key_field in init_params:
            return init_params[key_field]
        return None

    def leave_Annotation(self, _original_node, updated_node):
        replacer = AnnotationNameReplacer(self._get_key_type)
        new_ann = updated_node.annotation.visit(replacer)
        return updated_node.with_changes(annotation=new_ann)

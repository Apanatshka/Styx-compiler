"""
Transformer classes for the Styx transpiler.
"""

import libcst as cst
import libcst.matchers as m


def normalize_function_body(node: cst.FunctionDef) -> cst.FunctionDef:
    """Convert inline function bodies (SimpleStatementSuite) to IndentedBlock.

    Inline definitions like `def f(): return x` parse as SimpleStatementSuite.
    The rest of the compiler assumes IndentedBlock, so we normalize here.
    """
    if isinstance(node.body, cst.SimpleStatementSuite):
        new_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.body.body))])
        return node.with_changes(body=new_body)
    return node


def normalize_inline_if(node: cst.If) -> cst.If:
    """Convert inline if/elif/else bodies (SimpleStatementSuite) to IndentedBlock.

    Inline ifs like `if x: return False` parse with SimpleStatementSuite.
    The processor and other transforms assume IndentedBlock, so we normalize here.
    """
    # Normalize the if-body
    if isinstance(node.body, cst.SimpleStatementSuite):
        new_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.body.body))])
        node = node.with_changes(body=new_body)

    # Normalize the else/elif
    if node.orelse is not None:
        if isinstance(node.orelse, cst.Else) and isinstance(node.orelse.body, cst.SimpleStatementSuite):
            new_else_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.orelse.body.body))])
            node = node.with_changes(orelse=node.orelse.with_changes(body=new_else_body))
        elif isinstance(node.orelse, cst.If):
            node = node.with_changes(orelse=normalize_inline_if(node.orelse))

    return node


class ReturnHandlerTransformer(cst.CSTTransformer):
    """
    Finds 'return' statements and wraps them with the reply_to stack logic.
    Also injects 'ctx.put(state)' immediately before the logic.
    """

    def __init__(self, uses_state: bool):
        super().__init__()
        self.uses_state = uses_state

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
        # Handle ctx.call_remote_async: prepend ctx.put(__state__) if uses_state
        if self._is_call_remote_async(updated_node) and self.uses_state:
            put_state = cst.parse_statement("ctx.put(__state__)")
            return cst.FlattenSentinel([put_state, updated_node])

        return_node = None
        for node in updated_node.body:
            if isinstance(node, cst.Return):
                return_node = node
                break

        if not return_node:
            return updated_node

        ret_val = return_node.value if return_node.value else cst.Name("None")

        # Ensure implicit tuples (return a, b) get parenthesized so they
        # become a single argument: send_reply(ctx, reply_to, (a, b))
        if isinstance(ret_val, cst.Tuple) and not ret_val.lpar:
            ret_val = ret_val.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])

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

        put_state = cst.parse_statement("ctx.put(__state__)")

        return cst.FlattenSentinel([put_state, res_stmt]) if self.uses_state else res_stmt

    def leave_FunctionDef(self, _original_node, updated_node):
        body_stmts = updated_node.body.body
        last_stmt = body_stmts[-1] if body_stmts else None

        if not self._is_graph_terminal(last_stmt):
            new_body = list(updated_node.body.body)
            # Add send_reply for functions with reply_to so the reply chain isn't lost
            if self._has_reply_to_param(updated_node):
                if self.uses_state:
                    new_body.append(cst.parse_statement("ctx.put(__state__)"))
                new_body.append(cst.parse_statement("return send_reply(ctx, reply_to, None)"))
            elif self.uses_state:
                # No reply_to but state used — just flush state
                new_body.append(cst.parse_statement("ctx.put(__state__)"))

            if new_body != list(updated_node.body.body):
                return updated_node.with_changes(body=updated_node.body.with_changes(body=new_body))

        return updated_node


class RemoteCallLinearizer(cst.CSTTransformer):
    """
    Linearizes remote calls within functions.
    """

    def __init__(self, entities: dict[str, str] | None = None):
        self.entities = entities or {}
        self.call_counter = 0
        self.current_class: str | None = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.current_class = node.name.value
        return True

    def leave_ClassDef(self, _original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.current_class = None
        return updated_node

    def leave_FunctionDef(self, _original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        """Process each function and linearize remote calls."""
        if self.current_class is not None and self.current_class not in self.entities:
            return updated_node

        # Normalize inline function bodies (SimpleStatementSuite -> IndentedBlock)
        updated_node = normalize_function_body(updated_node)

        self.call_counter = 0

        linearizer = StatementLinearizer(self.entities)
        new_body = updated_node.body.visit(linearizer)

        return updated_node.with_changes(body=new_body)


class StatementLinearizer(cst.CSTTransformer):
    """
    Linearize method calls within statements.
    """

    def __init__(self, entities: dict[str, str] | None = None):
        self.entities = entities or {}
        self.counter = 1

    def leave_SimpleStatementLine(
        self, _original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.SimpleStatementLine]:
        """Process each statement and extract method calls."""
        new_statements = []

        for stmt in updated_node.body:
            extractor = CallExtractorAndReplacer(self.entities, self.counter)
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
                    and isinstance(new_stmt.targets[0].target, cst.Name)
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
        # Normalize inline if bodies (SimpleStatementSuite -> IndentedBlock)
        updated_node = normalize_inline_if(updated_node)

        new_statements = []

        extractor = CallExtractorAndReplacer(self.entities, self.counter)
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

    def leave_While(
        self, _original_node: cst.While, updated_node: cst.While
    ) -> cst.While | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle while statements specially, extracting from test condition."""
        new_statements = []

        extractor = CallExtractorAndReplacer(self.entities, self.counter)
        new_test = updated_node.test.visit(extractor)
        self.counter = extractor.counter

        if not extractor.extracted_calls:
            return updated_node

        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_statements.append(assignment)

        new_body_stmts = list(updated_node.body.body)
        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_body_stmts.append(assignment)

        new_body = updated_node.body.with_changes(body=new_body_stmts)
        new_while = updated_node.with_changes(test=new_test, body=new_body)
        new_statements.append(new_while)

        return cst.FlattenSentinel(new_statements)

    def leave_For(
        self, _original_node: cst.For, updated_node: cst.For
    ) -> cst.For | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle for statements specially, extracting from iter condition."""
        new_statements = []

        extractor = CallExtractorAndReplacer(self.entities, self.counter)
        new_iter = updated_node.iter.visit(extractor)
        self.counter = extractor.counter

        if not extractor.extracted_calls:
            return updated_node

        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_statements.append(assignment)

        new_for = updated_node.with_changes(iter=new_iter)
        new_statements.append(new_for)

        return cst.FlattenSentinel(new_statements)


class CallExtractorAndReplacer(cst.CSTTransformer):
    """
    Extract and replace method calls with variables.
    """

    def __init__(self, entities: dict[str, str] | None = None, start_counter=1):
        self.entities = entities or {}
        self.extracted_calls: list[tuple[str, cst.BaseExpression]] = []
        self.counter = start_counter
        self._in_send_async = False

    def visit_Call(self, node: cst.Call) -> bool:
        """Set flag when entering send_async() to prevent extraction of inner calls."""
        if isinstance(node.func, cst.Name) and node.func.value == "send_async":
            self._in_send_async = True
        return True

    def leave_Call(self, _original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        # Don't extract send_async itself — clear flag and return unchanged
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value == "send_async":
            self._in_send_async = False
            return updated_node

        # Don't extract calls inside send_async
        if self._in_send_async:
            return updated_node

        # Don't extract self.__key__()
        if (
            isinstance(updated_node.func, cst.Attribute)
            and isinstance(updated_node.func.value, cst.Name)
            and updated_node.func.value.value == "self"
            and updated_node.func.attr.value == "__key__"
        ):
            return updated_node

        is_entity_instantiation = False
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value in self.entities:
            is_entity_instantiation = True

        if is_entity_instantiation:
            receiver_var_name = f"attr_{self.counter}"
            self.counter += 1
            self.extracted_calls.append((receiver_var_name, updated_node))
            return cst.Name(receiver_var_name)

        if isinstance(updated_node.func, cst.Attribute):
            receiver = updated_node.func.value
            new_func = updated_node.func

            # Don't extract simple names or self.attribute as receivers
            is_simple_receiver = False
            if isinstance(receiver, cst.Name) or (
                isinstance(receiver, cst.Attribute)
                and isinstance(receiver.value, cst.Name)
                and receiver.value.value == "self"
            ):
                is_simple_receiver = True

            if not is_simple_receiver:
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
    3. self.__key__() -> ctx.key
    4. get_enity_by_key(Entity, key) -> key
    """

    def __init__(self, metadata=None, entity_keys=None, entity_init_params=None):
        super().__init__()
        self.metadata = metadata or {}
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

    def _get_node_type(self, node):
        """Helper to get the type name from mypy metadata or CST node type (for literals)."""
        mypy_type = self.metadata.get(node)
        if mypy_type:
            # Simple extraction for the print statement
            fullname = mypy_type.fullname
            return fullname.rsplit(".", 1)[-1]

        # Fallback for literals
        if isinstance(node, cst.SimpleString):
            return "str"
        if isinstance(node, cst.Integer):
            return "int"
        if isinstance(node, cst.Float):
            return "float"
        if isinstance(node, cst.Name) and node.value in ("True", "False"):
            return "bool"
        return "None"

    def leave_Call(self, original_node, updated_node):
        # Handles self.__key__() -> ctx.key
        if m.matches(original_node, m.Call(func=m.Attribute(value=m.Name("self"), attr=m.Name("__key__")))):
            return cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))

        # Handles get_enity_by_key(Entity, key) -> key
        if m.matches(original_node, m.Call(func=m.Name("get_enity_by_key"))) and len(updated_node.args) >= 2:
            entity_node = updated_node.args[0].value
            key = updated_node.args[1].value

            if isinstance(entity_node, cst.Name):
                entity_name = entity_node.value
                key_attrs = self.entity_keys.get(entity_name, [])
                init_params = self.entity_init_params.get(entity_name, {})
                expected_types = [init_params.get(a) for a in key_attrs]

                # 1. Structure validation
                if len(key_attrs) > 1:
                    if not isinstance(key, cst.Tuple):
                        actual_type = self._get_node_type(key)
                        msg = (
                            f"get_entity_by_key for {entity_name} expects a tuple "
                            f"for composite key, but got {actual_type}"
                        )
                        raise TypeError(msg)

                    if len(key.elements) != len(key_attrs):
                        msg = (
                            f"get_entity_by_key for {entity_name} expects "
                            f"{len(key_attrs)} elements, but got {len(key.elements)}"
                        )
                        raise TypeError(msg)

                # 2. Collect types for comparison and reporting
                if isinstance(key, cst.Tuple):
                    actual_types = []
                    original_tuple = original_node.args[1].value
                    for i, _element in enumerate(key.elements):
                        original_element = original_tuple.elements[i].value
                        actual_types.append(self._get_node_type(original_element))

                    # Check for mismatches
                    for _i, (actual, expected) in enumerate(zip(actual_types, expected_types, strict=True)):
                        if actual != expected:
                            expected_str = f"({', '.join(expected_types)})"
                            actual_str = f"({', '.join(actual_types)})"
                            msg = (
                                f"Type mismatch for retrieving '{entity_name}' by key: "
                                f"expected {expected_str}, got {actual_str}"
                            )
                            raise TypeError(msg)
                else:
                    original_key = original_node.args[1].value
                    actual_type = self._get_node_type(original_key)
                    expected_type = expected_types[0] if expected_types else None
                    if expected_type and actual_type != expected_type:
                        msg = (
                            f"Type mismatch for retrieving '{entity_name}' by key: "
                            f"expected {expected_type}, got {actual_type}"
                        )
                        raise TypeError(msg)

            return updated_node.args[1].value

        return updated_node

    def leave_AnnAssign(self, _original_node, updated_node):
        # If the target is transformed to a Subscript (like state['var']),
        # we must convert the AnnAssign to a regular Assign
        if isinstance(updated_node.target, cst.Subscript):
            value = updated_node.value if updated_node.value is not None else cst.Name("None")
            return cst.Assign(targets=[cst.AssignTarget(target=updated_node.target)], value=value)
        return updated_node

    def leave_Attribute(self, original_node, updated_node):
        # Handles self.attribute -> __state__['attribute']
        if m.matches(original_node, m.Attribute(value=m.Name("self"))):
            return cst.Subscript(
                value=cst.Name("__state__"),
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

    def __init__(
        self,
        entity_keys: dict[str, str],
        entity_init_params: dict[str, dict[str, str]],
        entity_key_types: dict[str, str] | None = None,
    ):
        super().__init__()
        self.entity_keys = entity_keys
        self.entity_init_params = entity_init_params
        self.entity_key_types = entity_key_types or {}

    def _get_key_type(self, entity_name: str):
        """Resolve an entity name to its key's type string, or None."""
        # 1. Check if we explicitly found a return type for __key__
        if entity_name in self.entity_key_types:
            return self.entity_key_types[entity_name]

        # 2. Check if the key field is in __init__ params
        key_fields = self.entity_keys.get(entity_name)
        init_params = self.entity_init_params.get(entity_name)

        if key_fields and isinstance(key_fields, list):
            if len(key_fields) > 1:
                # Composite keys are concatenated into strings
                return "str"

            # Single key: use its original type
            key_field = key_fields[0]
            if init_params and key_field in init_params:
                return init_params[key_field]

        # 3. Fallback: if it's a known entity, default to str
        if entity_name in self.entity_keys:
            return "str"

        return None

    def leave_Annotation(self, _original_node, updated_node):
        replacer = AnnotationNameReplacer(self._get_key_type)
        new_ann = updated_node.annotation.visit(replacer)
        return updated_node.with_changes(annotation=new_ann)

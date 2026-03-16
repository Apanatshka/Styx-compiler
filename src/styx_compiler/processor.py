import libcst as cst


class FunctionProcessor:
    """
    Actively slices a function into asynchronous steps using locals().update()
    """

    def __init__(
        self,
        original_func: cst.FunctionDef,
        class_name: str,
        entities: dict[str, str],
        metadata: dict,
        entity_keys: dict[str, str] = None,
        entity_init_params: dict[str, list[str]] = None,
    ):
        self.original_func = original_func
        self.class_name = class_name

        # Track entities marked with @entity decorator
        self.entities = entities
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

        # Mypy metadata: cst.CSTNode -> MypyType
        self.metadata = metadata

        self.split_counter = 1
        self.loop_iter_counter = 0
        self.generated_functions: list[cst.FunctionDef] = []

        # Track local variables to save/restore
        self.defined_vars = set()

        # Pre-scan arguments to define variables
        for param in original_func.params.params:
            if param.name.value != "self" and param.name.value != "ctx":
                self.defined_vars.add(param.name.value)

    def process(self) -> list[cst.FunctionDef]:
        """
        Main logic: Scans, Splits, and Returns a list of functions.
        """
        body = list(self.original_func.body.body)
        new_body = self._split_body(body)

        modified = self.original_func.with_changes(body=cst.IndentedBlock(body=new_body))
        # Sort by step number so output is step_2, step_3, ...
        self.generated_functions.sort(key=lambda f: int(f.name.value.rsplit("_", 1)[-1]))
        return [modified] + self.generated_functions

    def _split_body(self, body: list, loop_context=None) -> list:
        """
        Scan the function for remote calls and split the function when one is detected.

        loop_context: None for normal mode, or (loop_step_name, op_name, iter_key)
                      for loop mode where the tail dispatches back to the loop step.
        """
        for i, stmt in enumerate(body):
            # Remote call at top level
            if self._is_remote_call(stmt):
                return self._handle_remote_call(body, i, loop_context)

            # If-statement with remote calls in any branch
            if isinstance(stmt, cst.If) and self._if_contains_remote_call(stmt):
                return self._handle_if(body, i, loop_context)

            # For-loop with remote calls in body
            if isinstance(stmt, cst.For) and self._for_contains_remote_call(stmt):
                return self._handle_for(body, i, loop_context)

            self._track_vars(stmt)

        # No remote calls found
        if loop_context:
            # If body ends with a raise, skip unreachable continuation tail
            if body and self._ends_with_raise(body):
                return body
            # Loop mode: direct continuation back to loop step (same entity)
            loop_step_name, op_name, _ = loop_context
            put_state = cst.parse_statement("ctx.put(state)")
            direct_call = self._create_direct_continuation_call(loop_step_name)
            return body + [put_state] + [direct_call]
        return body

    def _handle_remote_call(self, body: list, i: int, loop_context=None) -> list:
        """Split at a remote call found at index i in body."""
        stmt = body[i]
        post_split = body[i + 1 :]
        has_continuation = len(post_split) > 0

        target_var, call_node, receiver, remote_method = self._extract_call_info(stmt)

        if has_continuation:
            self.split_counter += 1
            next_func_name = f"{self.original_func.name.value}_step_{self.split_counter}"

            dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, next_func_name)

            restore_block = self._create_restore_block()

            if target_var != "placeholder_return":
                self.defined_vars.add(target_var)

            cont_body = restore_block + self._split_body(post_split, loop_context)

            cont_func = self._create_continuation(next_func_name, cont_body, target_var)
            self.generated_functions.append(cont_func)

            return body[:i] + dispatch_block
        # Last remote call
        if loop_context:
            loop_step_name, _, _ = loop_context
            dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, loop_step_name)
        else:
            dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, "None")

        return body[:i] + dispatch_block

    def _handle_if(self, body: list, i: int, loop_context=None) -> list:
        """Split at an if-statement (at index i) whose branches contain remote calls."""
        pre_if = body[:i]
        result = self._process_if_node(body[i], body[i + 1 :], loop_context)
        return pre_if + result

    def _process_if_node(self, if_stmt, post_if, loop_context=None):
        """Recursively process an if/elif node. Returns a list of statements."""
        if_body_stmts = list(if_stmt.body.body)
        if_branch_dispatches = self._any_remote_call(if_body_stmts)

        # Snapshot before branching
        saved_vars = self.defined_vars.copy()

        # Process if-branch
        new_if_body = self._split_body(if_body_stmts + post_if, loop_context)

        # Restore for else/elif branch
        self.defined_vars = saved_vars.copy()

        # Process else/elif branch
        new_else = None
        if if_stmt.orelse is not None:
            if isinstance(if_stmt.orelse, cst.Else):
                else_body_stmts = list(if_stmt.orelse.body.body)
                new_else_body = self._split_body(else_body_stmts + post_if, loop_context)
                new_else = cst.Else(body=cst.IndentedBlock(body=new_else_body))
            elif isinstance(if_stmt.orelse, cst.If):
                # elif chain
                elif_result = self._process_if_node(if_stmt.orelse, post_if, loop_context)
                if len(elif_result) == 1 and isinstance(elif_result[0], cst.If):
                    new_else = elif_result[0]
                else:
                    new_else = cst.Else(body=cst.IndentedBlock(body=elif_result))
        elif if_branch_dispatches and post_if:
            # No else but if-branch dispatches — put post-if in else to prevent fallthrough
            processed_post_if = self._split_body(post_if, loop_context)
            new_else = cst.Else(body=cst.IndentedBlock(body=processed_post_if))

        # Restore to pre-branch state (caller determines what's defined after)
        self.defined_vars = saved_vars

        new_if = if_stmt.with_changes(body=cst.IndentedBlock(body=new_if_body), orelse=new_else)

        if if_stmt.orelse is not None or (if_branch_dispatches and post_if):
            return [new_if]
        return [new_if] + post_if

    def _parse_loop_iter(self, iter_node):
        """Returns (start_expr, bound_expr, is_range). Supports range() and collection iteration."""
        if isinstance(iter_node, cst.Call) and isinstance(iter_node.func, cst.Name) and iter_node.func.value == "range":
            if len(iter_node.args) == 1:
                return cst.Integer("0"), iter_node.args[0].value, True
            if len(iter_node.args) >= 2:
                return iter_node.args[0].value, iter_node.args[1].value, True

        # Collection iteration (for item in items)
        bound = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=iter_node)])
        return cst.Integer("0"), bound, False

    def _handle_for(self, body: list, i: int, loop_context=None) -> list:
        """
        Split at a for-loop whose body contains remote calls.
        """
        for_stmt = body[i]
        pre_loop = body[:i]
        post_loop = body[i + 1 :]

        op_name = self.entities[self.class_name]

        # Current loop level
        self.loop_iter_counter += 1
        iter_var_name = f"__loop_index_{self.loop_iter_counter}"

        self.split_counter += 1
        loop_step_name = f"{self.original_func.name.value}_step_{self.split_counter}"

        start_expr, bound_expr, is_range = self._parse_loop_iter(for_stmt.iter)

        # Init block: iter_var_name = start_expr
        init_iter = cst.SimpleStatementLine(
            body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(iter_var_name))], value=start_expr)]
        )
        self.defined_vars.add(iter_var_name)

        # Direct call to looping part of the function
        direct_call = self._create_direct_continuation_call(loop_step_name)
        restore_block = self._create_restore_block()

        # Determine loop variable name and type (but don't add to defined_vars yet)
        loop_var_name = "_loop_var"
        if isinstance(for_stmt.target, cst.Name):
            loop_var_name = for_stmt.target.value

        # Generate assignment for the loop var and increment
        state_idx_access = cst.Name(iter_var_name)

        if is_range:
            var_val = state_idx_access
        else:
            var_val = cst.Subscript(
                value=for_stmt.iter, slice=[cst.SubscriptElement(slice=cst.Index(value=state_idx_access))]
            )

        var_assign = cst.SimpleStatementLine(
            body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(loop_var_name))], value=var_val)]
        )

        inc_idx = cst.SimpleStatementLine(
            body=[cst.AugAssign(target=state_idx_access, operator=cst.AddAssign(), value=cst.Integer("1"))]
        )

        # Snapshot before processing branches
        saved_vars = self.defined_vars.copy()

        # Post-loop code: processed with the OUTER loop_context (not this loop's)
        if post_loop:
            post_loop_body = self._split_body(post_loop, loop_context)
        else:
            post_loop_body = [cst.SimpleStatementLine(body=[cst.Return(value=None)])]

        # Restore before processing loop body (independent path)
        self.defined_vars = saved_vars.copy()

        # Track loop variable for the loop body
        if loop_var_name != "_loop_var":
            self.defined_vars.add(loop_var_name)

        # Process loop body in loop mode
        inner_loop_context = (loop_step_name, op_name, iter_var_name)
        loop_body_stmts = list(for_stmt.body.body)
        loop_body_processed = self._split_body(loop_body_stmts, inner_loop_context)

        # Restore to pre-branch state
        self.defined_vars = saved_vars

        # Use if/else structure for bounds checking
        loop_condition = cst.Comparison(
            left=state_idx_access,
            comparisons=[cst.ComparisonTarget(operator=cst.GreaterThanEqual(), comparator=bound_expr)],
        )

        if_block = cst.If(
            test=loop_condition,
            body=cst.IndentedBlock(body=post_loop_body),
            orelse=cst.Else(body=cst.IndentedBlock(body=[var_assign, inc_idx] + loop_body_processed)),
        )
        loop_step_body = restore_block + [if_block]

        cont_func = self._create_continuation(loop_step_name, loop_step_body, "placeholder_return")
        self.generated_functions.append(cont_func)

        return pre_loop + [init_iter] + [direct_call]

    def _create_direct_continuation_call(self, func_name: str):
        """
        Create a call_remote_async to a continuation on the same entity,
        without building a reply_to entry.
        """
        op_name = self.entities[self.class_name]

        context_entries = [
            cst.DictElement(key=cst.SimpleString(f"'{v}'"), value=cst.Name(v)) for v in sorted(self.defined_vars)
        ]
        context_dict = cst.Dict(elements=context_entries)

        params_tuple = cst.Tuple(
            elements=[
                cst.Element(value=context_dict),
                cst.Element(value=cst.Name("None")),
                cst.Element(value=cst.Name("reply_to")),
            ]
        )

        return cst.SimpleStatementLine(
            body=[
                cst.Expr(
                    value=cst.Call(
                        func=cst.parse_expression("ctx.call_remote_async"),
                        args=[
                            cst.Arg(keyword=cst.Name("operator_name"), value=cst.SimpleString(f"'{op_name}'")),
                            cst.Arg(keyword=cst.Name("function_name"), value=cst.SimpleString(f"'{func_name}'")),
                            cst.Arg(
                                keyword=cst.Name("key"),
                                value=cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key")),
                            ),
                            cst.Arg(keyword=cst.Name("params"), value=params_tuple),
                        ],
                    )
                )
            ]
        )

    def _any_remote_call(self, stmts: list) -> bool:
        """Recursively check if any statement in the list contains a remote call."""
        for stmt in stmts:
            if self._is_remote_call(stmt):
                return True
            if isinstance(stmt, cst.If):
                if self._any_remote_call(list(stmt.body.body)):
                    return True
                if stmt.orelse is not None:
                    if isinstance(stmt.orelse, cst.Else):
                        if self._any_remote_call(list(stmt.orelse.body.body)):
                            return True
                    elif isinstance(stmt.orelse, cst.If):
                        if self._any_remote_call([stmt.orelse]):
                            return True
            if isinstance(stmt, cst.For):
                if self._for_contains_remote_call(stmt):
                    return True
        return False

    def _if_contains_remote_call(self, node: cst.If) -> bool:
        """Check if any branch of an if-statement contains remote calls."""
        if self._any_remote_call(list(node.body.body)):
            return True
        if node.orelse is not None:
            if isinstance(node.orelse, cst.Else):
                if self._any_remote_call(list(node.orelse.body.body)):
                    return True
            elif isinstance(node.orelse, cst.If):
                if self._if_contains_remote_call(node.orelse):
                    return True
        return False

    def _for_contains_remote_call(self, node: cst.For) -> bool:
        """Check if a For loop's body contains remote calls."""
        return self._any_remote_call(list(node.body.body))

    # ── Helpers ───────────────────────────────────────────────────────

    def _extract_call_info(self, stmt):
        element = stmt.body[0]
        if isinstance(element, cst.Assign):
            target_var = element.targets[0].target.value
            call_node = element.value
        elif isinstance(element, cst.Expr):
            target_var = "placeholder_return"
            call_node = element.value
        else:
            raise ValueError(f"Unexpected element: {type(element)}")

        if isinstance(call_node.func, cst.Name):
            receiver = call_node.func
            remote_method = "create"
        elif isinstance(call_node.func, cst.Attribute):
            receiver = call_node.func.value
            remote_method = call_node.func.attr.value
        else:
            raise ValueError(f"Unsupported call type: {type(call_node.func)}")

        return target_var, call_node, receiver, remote_method

    def _track_vars(self, stmt):
        """Track variable existence only (for context saving)."""
        if isinstance(stmt, cst.SimpleStatementLine):
            for element in stmt.body:
                if isinstance(element, cst.Assign):
                    for target in element.targets:
                        if isinstance(target.target, cst.Name):
                            self.defined_vars.add(target.target.value)
        elif isinstance(stmt, cst.If):
            for s in stmt.body.body:
                self._track_vars(s)
            if stmt.orelse is not None:
                if isinstance(stmt.orelse, cst.Else):
                    for s in stmt.orelse.body.body:
                        self._track_vars(s)
                elif isinstance(stmt.orelse, cst.If):
                    self._track_vars(stmt.orelse)
        elif isinstance(stmt, cst.For):
            if isinstance(stmt.target, cst.Name):
                self.defined_vars.add(stmt.target.value)
            for s in stmt.body.body:
                self._track_vars(s)

    def _ends_with_raise(self, body: list) -> bool:
        """Check if the last statement is a raise (making any following code unreachable)."""
        if not body:
            return False
        last = body[-1]
        if isinstance(last, cst.SimpleStatementLine):
            return any(isinstance(el, cst.Raise) for el in last.body)
        return False

    def _is_remote_call(self, stmt):

        if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
            return False

        element = stmt.body[0]
        if isinstance(element, (cst.Assign, cst.Expr)):
            val = element.value
        else:
            return False

        if not isinstance(val, cst.Call):
            return False

        # Case 1: entity initialization, item = Item(name, price)
        if isinstance(val.func, cst.Name):
            return val.func.value in self.entities

        # Case 2: Attribute calls (obj.method() or self.obj.method())
        # Use mypy metadata to check if the receiver evaluates to an entity type
        if isinstance(val.func, cst.Attribute):
            receiver = val.func.value
            return self._is_entity_node(receiver)

        return False

    def _is_entity_node(self, node) -> bool:
        """Check if a CST node's mypy type resolves to an entity."""
        return self._get_entity_type(node) is not None

    def _get_entity_type(self, node) -> str:
        mypy_type = self.metadata.get(node)

        if mypy_type is not None:
            # Only check the outermost type - e.g. list[Item] gives 'list', not 'Item'
            # This prevents list.append() / list.sort() from being treated as entity calls
            type_name = self._extract_outermost_type_name(mypy_type)
            if type_name in self.entities:
                return type_name

        if isinstance(node, cst.Call) and isinstance(node.func, cst.Name):
            if node.func.value in self.entities:
                return node.func.value

        return None

    def _extract_outermost_type_name(self, mypy_type) -> str:
        """Extract the outermost class name, ignoring generics.
        e.g. 'builtins.list[module.Item]' -> 'list'
             'test_tmp.Item' -> 'Item'
        """
        fullname = mypy_type.fullname
        # Drop generic part: 'builtins.list[module.Item]' -> 'builtins.list'
        if "[" in fullname:
            fullname = fullname.split("[")[0]
        return fullname.rsplit(".", 1)[-1]

    def _resolve_operator_name(self, receiver: cst.BaseExpression) -> str:
        # Case: Item() -> receiver is cst.Name(value="Item"), entity constructor
        if isinstance(receiver, cst.Name) and receiver.value in self.entities:
            return self.entities[receiver.value]

        # Use mypy metadata to resolve the receiver's type
        type_name = self._get_entity_type(receiver)
        if type_name:
            return self.entities[type_name]

        return self.entities.get(self.class_name, "unknown_operator")

    def _resolve_key_for_call(self, receiver, call_node, method):
        """
        Resolve the correct key= argument for a remote call.
        For constructor calls (method='create'), look up which __init__ param
        is the key field and pick the corresponding argument from the call.
        For method calls, the receiver variable IS the key.
        """
        if method == "create" and isinstance(receiver, cst.Name):
            entity_class = receiver.value
            key_field = self.entity_keys.get(entity_class)
            init_params = self.entity_init_params.get(entity_class)

            if key_field and init_params and key_field in init_params:
                param_names = list(init_params.keys())
                key_index = param_names.index(key_field)
                if key_index < len(call_node.args):
                    return call_node.args[key_index].value

        # Fallback: use the receiver itself (works for method calls like item.get_price())
        return receiver

    def _create_dispatch_block(self, receiver, method, call_node, next_func_name):
        # Resolve the correct key for this call
        key_value = self._resolve_key_for_call(receiver, call_node, method)

        if not call_node.args:
            params_value = cst.Tuple(elements=[cst.Element(value=cst.Name("reply_to"))])
        else:
            original_args = [arg.value for arg in call_node.args]

            tuple_elements = [cst.Element(value=value) for value in original_args] + [
                cst.Element(value=cst.Name("reply_to"))
            ]

            params_value = cst.Tuple(elements=tuple_elements)

        op_name = self._resolve_operator_name(receiver)
        reply_op_name = self.entities[self.class_name]

        # all defined variables and parameters to preserve context
        context_entries = [
            cst.DictElement(
                key=cst.SimpleString(f"'{v}'"),
                value=cst.Name(v),
            )
            for v in self.defined_vars
        ]

        context_dict = cst.Dict(elements=context_entries)

        # remote call becomes an async statement
        async_call = cst.SimpleStatementLine(
            body=[
                cst.Expr(
                    value=cst.Call(
                        func=cst.parse_expression("ctx.call_remote_async"),
                        args=[
                            cst.Arg(keyword=cst.Name("operator_name"), value=cst.SimpleString(f"'{op_name}'")),
                            cst.Arg(keyword=cst.Name("function_name"), value=cst.SimpleString(f"'{method}'")),
                            cst.Arg(keyword=cst.Name("key"), value=key_value),
                            cst.Arg(keyword=cst.Name("params"), value=params_value),
                        ],
                    )
                )
            ]
        )

        if next_func_name == "None":
            return [async_call]

        push_call = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name("reply_to"))],
                    value=cst.Call(
                        func=cst.Name("push_continuation"),
                        args=[
                            cst.Arg(value=cst.Name("ctx")),
                            cst.Arg(value=cst.Name("reply_to")),
                            cst.Arg(value=cst.SimpleString(f"'{reply_op_name}'")),
                            cst.Arg(value=cst.SimpleString(f"'{next_func_name}'")),
                            cst.Arg(
                                value=cst.Attribute(
                                    value=cst.Name("ctx"),
                                    attr=cst.Name("key"),
                                )
                            ),
                            cst.Arg(value=context_dict),
                        ],
                    ),
                )
            ]
        )

        return [push_call, async_call]

    def _create_restore_block(self):
        """Restore locals from params dict"""
        restore_statements = []

        sorted_vars = sorted(self.defined_vars)

        if not sorted_vars:
            return restore_statements

        # params = resolve_context(ctx, func_context)
        restore_statements.append(
            cst.SimpleStatementLine(
                body=[
                    cst.Assign(
                        targets=[cst.AssignTarget(target=cst.Name("params"))],
                        value=cst.Call(
                            func=cst.Name("resolve_context"),
                            args=[
                                cst.Arg(value=cst.Name("ctx")),
                                cst.Arg(value=cst.Name("func_context")),
                            ],
                        ),
                    )
                ]
            )
        )

        # (var1, var2, ...) = (params['var1'], params['var2'], ...)
        restore_statements.append(
            cst.SimpleStatementLine(
                body=[
                    cst.Assign(
                        targets=[
                            cst.AssignTarget(
                                target=cst.Tuple(elements=[cst.Element(cst.Name(var)) for var in sorted_vars])
                            )
                        ],
                        value=cst.Tuple(
                            elements=[
                                cst.Element(
                                    cst.Subscript(
                                        value=cst.Name("params"),
                                        slice=[
                                            cst.SubscriptElement(slice=cst.Index(value=cst.SimpleString(f"'{var}'")))
                                        ],
                                    )
                                )
                                for var in sorted_vars
                            ]
                        ),
                    )
                ]
            )
        )

        return restore_statements

    def _create_continuation(self, name, body, target_var):
        """Create a continuation function"""
        op_name = self.entities[self.class_name] + "_operator"
        deco = cst.Decorator(decorator=cst.parse_expression(f"{op_name}.register"))

        reply_to_param = cst.Param(
            name=cst.Name("reply_to"),
            annotation=cst.Annotation(annotation=cst.Name("list")),
            default=cst.Name("None"),
        )

        return cst.FunctionDef(
            name=cst.Name(name),
            params=cst.Parameters(
                params=[
                    cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction"))),
                    cst.Param(name=cst.Name("func_context")),
                    cst.Param(name=cst.Name(target_var), default=cst.Name("None")),
                    reply_to_param,
                ]
            ),
            body=cst.IndentedBlock(body=body),
            decorators=[deco],
            asynchronous=cst.Asynchronous(),
        )

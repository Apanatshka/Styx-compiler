from typing import Dict, List, Tuple, Union
import libcst as cst

class FunctionProcessor:
    """
    Actively slices a function into asynchronous steps using locals().update()
    """
    
    def __init__(self, original_func: cst.FunctionDef, class_name: str, entities: Dict[str, str], self_attr_types: Dict[str, str], entity_keys: Dict[str, str] = None, entity_init_params: Dict[str, List[str]] = None):
        self.original_func = original_func
        self.class_name = class_name

        # Track entities marked with @entity decorator
        self.entities = entities
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

        self.split_counter = 1
        self.loop_iter_counter = 0
        self.generated_functions: List[cst.FunctionDef] = []
        
        # Track local variables to save/restore
        self.self_attr_types = self_attr_types # Entity variables
        self.defined_vars = set()  # Local variables
        self.local_types = {} # Local variable types
        self.local_collection_element_types = {}  # e.g. {"items": "Item"}

        # Pre-scan arguments to define types and initial variables
        for param in original_func.params.params:
            if param.name.value != 'self' and param.name.value != 'ctx':
                self.defined_vars.add(param.name.value)
            if param.annotation:
                ann = param.annotation.annotation
                if isinstance(ann, cst.Name):
                    self.local_types[param.name.value] = ann.value
                elif isinstance(ann, cst.Subscript) and isinstance(ann.value, cst.Name):
                    if ann.value.value in ("list", "List", "set", "Set"):
                        if ann.slice and len(ann.slice) == 1:
                            inner = ann.slice[0].slice
                            if isinstance(inner, cst.Index) and isinstance(inner.value, cst.Name):
                                element_type = inner.value.value
                                if element_type in self.entities:
                                    self.local_collection_element_types[param.name.value] = element_type

    def process(self) -> List[cst.FunctionDef]:
        """
        Main logic: Scans, Splits, and Returns a list of functions.
        """
        body = list(self.original_func.body.body)
        new_body = self._split_body(body)

        modified = self.original_func.with_changes(
            body=cst.IndentedBlock(body=new_body)
        )
        # Sort by step number so output is step_2, step_3, ...
        self.generated_functions.sort(key=lambda f: int(f.name.value.rsplit('_', 1)[-1]))
        return [modified] + self.generated_functions

    def _split_body(self, body: List, loop_context=None) -> List:
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
            # Loop mode: direct continuation back to loop step (same entity)
            loop_step_name, op_name, _ = loop_context
            put_state = cst.parse_statement("ctx.put(state)")
            direct_call = self._create_direct_continuation_call(loop_step_name)
            return body + [put_state] + [direct_call]
        else:
            return body

    def _handle_remote_call(self, body: List, i: int, loop_context=None) -> List:
        """Split at a remote call found at index i in body."""
        stmt = body[i]
        post_split = body[i+1:]
        has_continuation = len(post_split) > 0

        target_var, call_node, receiver, remote_method = self._extract_call_info(stmt)

        if has_continuation:
            self.split_counter += 1
            next_func_name = f"{self.original_func.name.value}_step_{self.split_counter}"

            dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, next_func_name)

            restore_block = self._create_restore_block()

            if target_var != "placeholder_return":
                self.defined_vars.add(target_var)
                if remote_method == "create" and isinstance(receiver, cst.Name):
                    self.local_types[target_var] = receiver.value

            cont_body = restore_block + self._split_body(post_split, loop_context)

            cont_func = self._create_continuation(next_func_name, cont_body, target_var)
            self.generated_functions.append(cont_func)

            return body[:i] + dispatch_block
        else:
            # Last remote call
            if loop_context:
                loop_step_name, _, _ = loop_context
                dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, loop_step_name)
            else:
                dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, "None")

            return body[:i] + dispatch_block

    def _handle_if(self, body: List, i: int, loop_context=None) -> List:
        """Split at an if-statement (at index i) whose branches contain remote calls."""
        pre_if = body[:i]
        result = self._process_if_node(body[i], body[i+1:], loop_context)
        return pre_if + result

    def _process_if_node(self, if_stmt, post_if, loop_context=None):
        """Recursively process an if/elif node. Returns a list of statements."""
        if_body_stmts = list(if_stmt.body.body)
        if_branch_dispatches = self._any_remote_call(if_body_stmts)
        new_if_body = self._split_body(if_body_stmts + post_if, loop_context)

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

        new_if = if_stmt.with_changes(
            body=cst.IndentedBlock(body=new_if_body),
            orelse=new_else
        )

        if if_stmt.orelse is not None or (if_branch_dispatches and post_if):
            return [new_if]
        else:
            return [new_if] + post_if

    def _handle_for(self, body: List, i: int, loop_context=None) -> List:
        """
        Split at a for-loop whose body contains remote calls.
        """
        for_stmt = body[i]
        pre_loop = body[:i]
        post_loop = body[i+1:]

        op_name = self.entities[self.class_name]

        # Current loop level
        self.loop_iter_counter += 1
        iter_key = f"'__loop_iter_{self.loop_iter_counter}'"

        self.split_counter += 1
        loop_step_name = f"{self.original_func.name.value}_step_{self.split_counter}"

        # Init block: state[iter_key] = iter(iterable)
        init_iter = cst.SimpleStatementLine(
            body=[cst.Assign(
                targets=[cst.AssignTarget(
                    target=cst.Subscript(
                        value=cst.Name("state"),
                        slice=[cst.SubscriptElement(
                            slice=cst.Index(value=cst.SimpleString(iter_key))
                        )]
                    )
                )],
                value=cst.Call(
                    func=cst.Name("iter"),
                    args=[cst.Arg(value=for_stmt.iter)]
                )
            )]
        )
        put_state = cst.parse_statement("ctx.put(state)")

        # Direct call to looping part of the function
        direct_call = self._create_direct_continuation_call(loop_step_name)
        restore_block = self._create_restore_block()

        # track loop variable (after context/restore are built)
        loop_var_name = "_loop_var"
        if isinstance(for_stmt.target, cst.Name):
            loop_var_name = for_stmt.target.value
            self.defined_vars.add(loop_var_name)
            if isinstance(for_stmt.iter, cst.Name):
                element_type = self.local_collection_element_types.get(for_stmt.iter.value)
                if element_type:
                    self.local_types[loop_var_name] = element_type

        # Build try: var = next(iter) / except StopIteration: <post-loop>
        next_assign = cst.SimpleStatementLine(
            body=[cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(loop_var_name))],
                value=cst.Call(
                    func=cst.Name("next"),
                    args=[cst.Arg(value=cst.Subscript(
                        value=cst.Name("state"),
                        slice=[cst.SubscriptElement(
                            slice=cst.Index(value=cst.SimpleString(iter_key))
                        )]
                    ))]
                )
            )]
        )

        # Post-loop code: processed with the OUTER loop_context (not this loop's)
        if post_loop:
            post_loop_body = self._split_body(post_loop, loop_context)
        else:
            post_loop_body = [cst.SimpleStatementLine(body=[cst.Return(value=None)])]

        # Process loop body in loop mode
        inner_loop_context = (loop_step_name, op_name, iter_key)
        loop_body_stmts = list(for_stmt.body.body)
        loop_body_processed = self._split_body(loop_body_stmts, inner_loop_context)

        # Structure depends on nesting: nested loops put body inside try to avoid fallthrough
        if loop_context is not None:
            # Nested: body inside try block
            try_block = cst.Try(
                body=cst.IndentedBlock(body=[next_assign] + loop_body_processed),
                handlers=[cst.ExceptHandler(
                    type=cst.Name("StopIteration"),
                    body=cst.IndentedBlock(body=post_loop_body)
                )]
            )
            loop_step_body = restore_block + [try_block]
        else:
            # Top-level: try/except then body (StopIteration returns/dispatches)
            try_block = cst.Try(
                body=cst.IndentedBlock(body=[next_assign]),
                handlers=[cst.ExceptHandler(
                    type=cst.Name("StopIteration"),
                    body=cst.IndentedBlock(body=post_loop_body)
                )]
            )
            loop_step_body = restore_block + [try_block] + loop_body_processed

        cont_func = self._create_continuation(loop_step_name, loop_step_body, "placeholder_return")
        self.generated_functions.append(cont_func)

        return pre_loop + [init_iter, put_state] + [direct_call]





    def _create_direct_continuation_call(self, func_name: str):
        """
        Create a call_remote_async to a continuation on the same entity,
        without building a reply_to entry.
        """
        op_name = self.entities[self.class_name]

        context_entries = [
            cst.DictElement(
                key=cst.SimpleString(f"'{v}'"),
                value=cst.Name(v)
            )
            for v in sorted(self.defined_vars)
        ]
        context_dict = cst.Dict(elements=context_entries)

        params_tuple = cst.Tuple(elements=[
            cst.Element(value=context_dict),
            cst.Element(value=cst.Name("None")),
            cst.Element(value=cst.Name("reply_to")),
        ])

        return cst.SimpleStatementLine(
            body=[cst.Expr(value=cst.Call(
                func=cst.parse_expression("ctx.call_remote_async"),
                args=[
                    cst.Arg(keyword=cst.Name("operator_name"), value=cst.SimpleString(f"'{op_name}'")),
                    cst.Arg(keyword=cst.Name("function_name"), value=cst.SimpleString(f"'{func_name}'")),
                    cst.Arg(keyword=cst.Name("key"), value=cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))),
                    cst.Arg(keyword=cst.Name("params"), value=params_tuple),
                ]
            ))]
        )

    def _any_remote_call(self, stmts: List) -> bool:
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
        """Check if a For loop's body contains remote calls, with temp type inference."""
        temp_types = {}
        
        # Temporarily infer loop variable type from direct iteration
        if isinstance(node.target, cst.Name) and isinstance(node.iter, cst.Name):
            loop_var_name = node.target.value
            element_type = self.local_collection_element_types.get(node.iter.value)
            if element_type and element_type in self.entities:
                temp_types[loop_var_name] = element_type
                self.local_types[loop_var_name] = element_type

        # Temporarily infer types from body assignments like item = cart[index]
        for stmt in node.body.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for el in stmt.body:
                    if isinstance(el, cst.Assign) and isinstance(el.value, cst.Subscript):
                        if isinstance(el.value.value, cst.Name):
                            collection_name = el.value.value.value
                            element_type = self.local_collection_element_types.get(collection_name)
                            if element_type and element_type in self.entities:
                                for target in el.targets:
                                    if isinstance(target.target, cst.Name):
                                        var_name = target.target.value
                                        temp_types[var_name] = element_type
                                        self.local_types[var_name] = element_type

        result = self._any_remote_call(list(node.body.body))

        # Clean up temporary types
        for var_name in temp_types:
            if var_name not in self.defined_vars:
                self.local_types.pop(var_name, None)
        return result

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
        if isinstance(stmt, cst.SimpleStatementLine):
            for element in stmt.body:
                if isinstance(element, cst.Assign):
                    assigned_type = None
                    # Constructor call: item = Item(...)
                    if isinstance(element.value, cst.Call) and isinstance(element.value.func, cst.Name):
                        assigned_type = element.value.func.value # e.g., "Item"
                    # Subscript access: item = cart[index] where cart: list[Item]
                    elif isinstance(element.value, cst.Subscript) and isinstance(element.value.value, cst.Name):
                        collection_name = element.value.value.value
                        element_type = self.local_collection_element_types.get(collection_name)
                        if element_type:
                            assigned_type = element_type

                    for target in element.targets:
                        if isinstance(target.target, cst.Name):
                            var_name = target.target.value
                            self.defined_vars.add(var_name)
                            if assigned_type:
                                self.local_types[var_name] = assigned_type
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
            # Infer loop variable type from iterable's collection element type
            if isinstance(stmt.target, cst.Name) and isinstance(stmt.iter, cst.Name):
                element_type = self.local_collection_element_types.get(stmt.iter.value)
                if element_type:
                    self.local_types[stmt.target.value] = element_type
                    self.defined_vars.add(stmt.target.value)
            for s in stmt.body.body:
                self._track_vars(s)

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

        # Case 2 & 3: Attribute calls, e.g., obj.run() or self.obj.run()
        if isinstance(val.func, cst.Attribute):
            base = val.func.value

            # Case 2: local variable (item.get_price())
            if isinstance(base, cst.Name):
                var_type = self.local_types.get(base.value)
                return var_type in self.entities

            # Case 3: self attribute (self.item.get_price())
            if isinstance(base, cst.Attribute) and isinstance(base.value, cst.Name):
                if base.value.value == "self":
                    attr_type = self.self_attr_types.get(base.attr.value)
                    return attr_type in self.entities

        return False
    
    def _resolve_operator_name(self, receiver: cst.BaseExpression) -> str:
        # Case: Item() -> receiver is cst.Name(value="Item")
        if isinstance(receiver, cst.Name):
            name_val = receiver.value
            # Is it a variable with a known type?
            if name_val in self.local_types:
                actual_type = self.local_types[name_val]
                return self.entities.get(actual_type, actual_type)
            # Is the name itself an entity (Class name)?
            if name_val in self.entities:
                return self.entities[name_val]

        # Case: self.item.method() -> receiver is cst.Attribute
        if isinstance(receiver, cst.Attribute):
            if isinstance(receiver.value, cst.Name) and receiver.value.value == "self":
                attr_name = receiver.attr.value
                attr_type = self.self_attr_types.get(attr_name)
                return self.entities.get(attr_type, attr_type)

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
            params_value = cst.Tuple(
                elements=[cst.Element(value=cst.Name("reply_to"))]
            )
        else:
            original_args = [arg.value for arg in call_node.args]

            tuple_elements = [
                cst.Element(value=value)
                for value in original_args
            ] + [
                cst.Element(value=cst.Name("reply_to"))
            ]

            params_value = cst.Tuple(elements=tuple_elements)

        op_name = self._resolve_operator_name(receiver)
        reply_op_name = self.entities[self.class_name]

        # all defined variables and parameters to preserve context
        context_entries = [
            cst.DictElement(
                key=cst.SimpleString(f"'{v}'"),
                value=cst.Name(v)
            )
            for v in self.defined_vars
        ]

        context_dict = cst.Dict(elements=context_entries)

        # reply to the continuation
        reply_info_dict = cst.Dict(elements=[
            cst.DictElement(cst.SimpleString("'op_name'"), cst.SimpleString(f"'{reply_op_name}'")),
            cst.DictElement(cst.SimpleString("'fun'"), cst.SimpleString(f"'{next_func_name}'")),
            cst.DictElement(
                key=cst.SimpleString("'id'"),
                value=cst.Attribute(
                    value=cst.Name("ctx"),
                    attr=cst.Name("key"),
                ),
            ),
            cst.DictElement(cst.SimpleString("'context'"), context_dict),
        ])

        init_reply_to = cst.If(
            test=cst.Comparison(
                left=cst.Name("reply_to"),
                comparisons=[
                    cst.ComparisonTarget(
                        operator=cst.Is(),
                        comparator=cst.Name("None")
                    )
                ],
            ),
            body=cst.IndentedBlock(
                body=[
                    cst.SimpleStatementLine(
                        body=[
                            cst.Assign(
                                targets=[cst.AssignTarget(cst.Name("reply_to"))],
                                value=cst.List(elements=[])
                            )
                        ]
                    )
                ]
            )
        )

        push_reply_info = cst.SimpleStatementLine(
            body=[cst.Expr(
                value=cst.Call(
                    func=cst.Attribute(value=cst.Name("reply_to"), attr=cst.Name("append")),
                    args=[cst.Arg(value=reply_info_dict)]
                )
            )]
        )

        put_state = cst.parse_statement("ctx.put(state)")

        # remote call becomes an async statement
        async_call = cst.SimpleStatementLine(
            body=[cst.Expr(
                value=cst.Call(
                    func=cst.parse_expression("ctx.call_remote_async"),
                    args=[
                        cst.Arg(keyword=cst.Name("operator_name"), value=cst.SimpleString(f"'{op_name}'")),
                        cst.Arg(keyword=cst.Name("function_name"), value=cst.SimpleString(f"'{method}'")),
                        cst.Arg(keyword=cst.Name("key"), value=key_value),
                        cst.Arg(keyword=cst.Name("params"), value=params_value),
                    ]
                )
            )]
        )

        if next_func_name == "None":
            return [async_call]

        return [init_reply_to, push_reply_info, async_call]

    def _create_restore_block(self):
        """Restore locals from params dict"""
        get_state = cst.parse_statement("state = ctx.get()")

        restore_statements = []

        sorted_vars = sorted(self.defined_vars)

        restore_statements.append(
            cst.SimpleStatementLine(
                body=[
                    cst.Assign(
                        targets=[
                            cst.AssignTarget(
                                target=cst.Tuple(
                                    elements=[
                                        cst.Element(cst.Name(var))
                                        for var in sorted_vars
                                    ]
                                )
                            )
                        ],
                        value=cst.Tuple(
                            elements=[
                                cst.Element(
                                    cst.Subscript(
                                        value=cst.Name("params"),
                                        slice=[
                                            cst.SubscriptElement(
                                                slice=cst.Index(
                                                    value=cst.SimpleString(f"'{var}'")
                                                )
                                            )
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


        return [get_state] + restore_statements

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
            params=cst.Parameters(params=[
                cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction"))),
                cst.Param(name=cst.Name("params")),
                cst.Param(name=cst.Name(target_var), default=cst.Name("None")),
                reply_to_param,
            ]),
            body=cst.IndentedBlock(body=body),
            decorators=[deco],
            asynchronous=cst.Asynchronous()
        )

from typing import Dict, List, Tuple, Union
import libcst as cst

class FunctionProcessor:
    """
    Actively slices a function into asynchronous steps using locals().update()
    """
    
    def __init__(self, original_func: cst.FunctionDef, class_name: str, entities: Dict[str, str], self_attr_types: Dict[str, str]):
        self.original_func = original_func
        self.class_name = class_name

        # Track entities marked with @entity decorator
        self.entities = entities

        self.split_counter = 1
        self.generated_functions: List[cst.FunctionDef] = []
        
        # Track local variables to save/restore
        self.self_attr_types = self_attr_types # Entity variables
        self.defined_vars = set()  # Local variables
        self.local_types = {} # Local variable types

        # Pre-scan arguments to define types and initial variables
        for param in original_func.params.params:
            if param.name.value != 'self' and param.name.value != 'ctx':
                self.defined_vars.add(param.name.value)
            if param.annotation and isinstance(param.annotation.annotation, cst.Name):
                self.local_types[param.name.value] = param.annotation.annotation.value

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

    def _split_body(self, body: List) -> List:
        """
        Scan the function for remote calls and split the function when one is detected.
        TODO: Handle loops
        """
        for i, stmt in enumerate(body):
            # Linear splitting: remote call at top level
            if self._is_remote_call(stmt):
                return self._handle_remote_call(body, i)

            # If-statement that contains remote calls in its branches
            if isinstance(stmt, cst.If) and self._contains_remote_call(stmt):
                return self._handle_if_with_remote_calls(body, i)

            self._track_vars(stmt)

        # No remote calls found — return body unchanged
        return body

    def _handle_remote_call(self, body: List, i: int) -> List:
        """
        Split at a remote call found at index i in body.
        """
        stmt = body[i]
        post_split = body[i+1:]
        has_continuation = len(post_split) > 0

        # Next function name
        if has_continuation:
            self.split_counter += 1
            next_func_name = f"{self.original_func.name.value}_step_{self.split_counter}"
        else:
            next_func_name = "None"

        # Extract call info and create dispatch
        target_var, call_node, receiver, remote_method = self._extract_call_info(stmt)
        dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, next_func_name)

        pre_split_body = body[:i] + dispatch_block

        # Create continuation if there's code after
        if has_continuation:
            restore_block = self._create_restore_block()
            cont_body = restore_block + post_split

            # Recursively split the continuation body
            cont_body = self._split_body(cont_body)

            cont_func = self._create_continuation(next_func_name, cont_body, target_var)
            self.generated_functions.append(cont_func)

        return pre_split_body

    def _handle_if_with_remote_calls(self, body: List, i: int) -> List:
        """
        Split at an if-statement (at index i) whose branches contain remote calls.
        """
        stmt = body[i]
        post_if_body = body[i+1:]
        pre_if_body = body[:i]

        new_body = self._process_if_with_remote_calls(stmt, post_if_body, pre_if_body)
        return new_body


    def _contains_remote_call(self, node) -> bool:
        """
        Recursively checks if a node (If statement, branch body, etc.) 
        contains any remote calls that need splitting.
        """
        if isinstance(node, cst.If):
            # Check the if-body
            for stmt in node.body.body:
                if self._is_remote_call(stmt):
                    return True
                if isinstance(stmt, cst.If) and self._contains_remote_call(stmt):
                    return True
            # Check elif/else
            if node.orelse is not None:
                if isinstance(node.orelse, cst.Else):
                    for stmt in node.orelse.body.body:
                        if self._is_remote_call(stmt):
                            return True
                        if isinstance(stmt, cst.If) and self._contains_remote_call(stmt):
                            return True
                elif isinstance(node.orelse, cst.If):
                    if self._contains_remote_call(node.orelse):
                        return True
            return False
        return False

    def _branch_has_remote_call(self, stmts: List) -> bool:
        """Check if a flat list of statements contains any remote call."""
        for stmt in stmts:
            if self._is_remote_call(stmt):
                return True
            if isinstance(stmt, cst.If) and self._contains_remote_call(stmt):
                return True
        return False

    def _process_if_with_remote_calls(self, if_stmt: cst.If, post_if_body: List, pre_if_body: List) -> List:
        """
        Process an if-statement whose branches contain remote calls.
        
        Strategy:
        - For each branch (if-body, else-body), scan for the first remote call
        - When found: emit the dispatch inside that branch,
          create a continuation function for the rest of that branch + post-if tail
        - When not found: inline the branch body + post-if tail
        - When there's no else and the if-branch dispatches, create an else
          with the post-if tail to prevent fallthrough after dispatch
        """
        
        # Process the if-true branch
        if_body_stmts = list(if_stmt.body.body)
        if_branch_dispatches = self._branch_has_remote_call(if_body_stmts)
        new_if_body = self._split_body(if_body_stmts + post_if_body)
        
        # Process else/elif branch  
        new_else = None
        if if_stmt.orelse is not None:
            if isinstance(if_stmt.orelse, cst.Else):
                else_body_stmts = list(if_stmt.orelse.body.body)
                new_else_body = self._split_body(else_body_stmts + post_if_body)
                new_else = cst.Else(
                    body=cst.IndentedBlock(body=new_else_body)
                )
            elif isinstance(if_stmt.orelse, cst.If):
                # elif chain: treat as nested if with the same post-if tail
                elif_body = self._process_if_with_remote_calls(
                    if_stmt.orelse, post_if_body, []
                )
                if len(elif_body) == 1 and isinstance(elif_body[0], cst.If):
                    new_else = elif_body[0]
                else:
                    new_else = cst.Else(
                        body=cst.IndentedBlock(body=elif_body)
                    )
        elif if_branch_dispatches and post_if_body:
            # Process the post-if body so remote calls within it are also split.
            processed_post_if = self._split_body(post_if_body)
            new_else = cst.Else(
                body=cst.IndentedBlock(body=processed_post_if)
            )

        # Rebuild the if statement
        new_if = if_stmt.with_changes(
            body=cst.IndentedBlock(body=new_if_body),
            orelse=new_else
        )
        
        if if_stmt.orelse is not None or (if_branch_dispatches and post_if_body):
            return pre_if_body + [new_if]
        else:
            return pre_if_body + [new_if] + post_if_body

    # ── Helpers ───────────────────────────────────────────────────────

    def _extract_call_info(self, stmt):
        """Extract target_var, call_node, receiver, remote_method from a remote call statement."""
        element = stmt.body[0]
        if isinstance(element, cst.Assign):
            target_var = element.targets[0].target.value
            call_node = element.value
        elif isinstance(element, cst.Expr):
            target_var = "placeholder_return"
            call_node = element.value
        else:
            raise ValueError(f"Unexpected element type in remote call: {type(element)}")

        receiver = call_node.func.value
        remote_method = call_node.func.attr.value
        return target_var, call_node, receiver, remote_method

    def _track_vars(self, stmt):
        """Simple tracker for variables assigned on the Left Hand Side"""
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

    def _is_remote_call(self, stmt):
        """
        Determines if a statement is a remote call that needs splitting.
        """
        if not isinstance(stmt, cst.SimpleStatementLine): 
            return False
        
        if not stmt.body:
            return False

        element = stmt.body[0]
        val = None

        if isinstance(element, cst.Assign):
            val = element.value
        elif isinstance(element, cst.Expr):
            val = element.value
        else:
            return False

        if not isinstance(val, cst.Call): 
            return False
            
        if not isinstance(val.func, cst.Attribute): 
            return False
        
        base = val.func.value

        # Case 1: local variable (existing behavior)
        if isinstance(base, cst.Name):
            var_name = base.value
            var_type = self.local_types.get(var_name)
            if var_type in self.entities:
                return True

        # Case 2: self.attribute
        if isinstance(base, cst.Attribute):
            if (
                isinstance(base.value, cst.Name)
                and base.value.value == "self"
            ):
                attr_name = base.attr.value
                attr_type = self.self_attr_types.get(attr_name)
                if attr_type in self.entities:
                    return True

        return False
    
    def _resolve_operator_name(self, receiver: cst.BaseExpression) -> str:
        """
        Determine operator name from receiver expression.
        """

        # Case 1: local variable  -> item.get_price()
        if isinstance(receiver, cst.Name):
            var_name = receiver.value
            var_type = self.local_types.get(var_name)
            if var_type in self.entities:
                return self.entities[var_type]

        # Case 2: self.attribute -> self.item.get_price()
        if isinstance(receiver, cst.Attribute):
            if (
                isinstance(receiver.value, cst.Name)
                and receiver.value.value == "self"
            ):
                attr_name = receiver.attr.value
                attr_type = self.self_attr_types.get(attr_name)
                if attr_type in self.entities:
                    return self.entities[attr_type]

        # fallback
        return self.entities.get(self.class_name)


    def _create_dispatch_block(self, receiver, method, call_node, next_func_name):

        if not call_node.args:
            params_value = cst.Name("reply_to")
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
                        cst.Arg(keyword=cst.Name("key"), value=receiver),
                        cst.Arg(keyword=cst.Name("params"), value=params_value),
                    ]
                )
            )]
        )

        if next_func_name == "None":
            return [async_call]

        return [push_reply_info, async_call]

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

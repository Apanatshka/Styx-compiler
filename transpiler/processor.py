from typing import Dict, List, Tuple, Union
import libcst as cst

class FunctionProcessor:
    """
    Actively slices a function into asynchronous steps using locals().update()
    """
    
    def __init__(self, original_func: cst.FunctionDef, class_name: str, entities: Dict[str, str]):
        self.original_func = original_func
        self.class_name = class_name
        self.entities = entities
        self.generated_functions: List[cst.FunctionDef] = []
        self.step_counter = 0
        
        # Track local variables to save/restore
        self.defined_vars = set() 
        self.local_types = {}

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
            current_body = list(self.original_func.body.body)
            
            first_func_modified = None
            
            i = 0
            while i < len(current_body):
                stmt = current_body[i]

                if self._is_remote_call(stmt):
                    # 1. Peek ahead to see if there is code left to execute
                    post_split_body = current_body[i+1:]
                    has_continuation = len(post_split_body) > 0

                    # 2. Determine function names based on whether we need a next step
                    if has_continuation:
                        self.step_counter += 1
                        next_func_name = f"{self.original_func.name.value}_step_{self.step_counter}"
                    else:
                        next_func_name = "None"  # Tell dispatcher there is no next step

                    # 3. Analyze the call node
                    element = stmt.body[0]
                    target_var = None
                    call_node = None

                    if isinstance(element, cst.Assign):
                        target_var = element.targets[0].target.value
                        call_node = element.value
                    elif isinstance(element, cst.Expr):
                        target_var = "placeholder_return"
                        call_node = element.value

                    remote_obj = call_node.func.value.value
                    remote_method = call_node.func.attr.value
                    
                    # 4. Create dispatch block using the determined next_func_name
                    dispatch_block = self._create_dispatch_block(remote_obj, remote_method, call_node, next_func_name)
                    
                    pre_split_body = current_body[:i] + dispatch_block

                    # 5. Modify the current function
                    if self.step_counter == (1 if has_continuation else 0):
                        # If this is the first split (or the only split), modify original
                        if first_func_modified is None:
                            first_func_modified = self.original_func.with_changes(
                                body=cst.IndentedBlock(body=pre_split_body)
                            )
                        else:
                            first_func_modified = first_func_modified.with_changes(
                                body=cst.IndentedBlock(body=pre_split_body)
                            )
                    else:
                        # Modify the most recently generated function
                        prev_func = self.generated_functions[-1]
                        self.generated_functions[-1] = prev_func.with_changes(
                            body=cst.IndentedBlock(body=pre_split_body)
                        )

                    # 6. If no code follows, we are done with this branch
                    if not has_continuation:
                        break

                    # 7. Otherwise, create the continuation function
                    restore_block = self._create_restore_block()
                    current_body = restore_block + post_split_body
                    
                    new_func = self._create_continuation(next_func_name, current_body, target_var)
                    self.generated_functions.append(new_func)
                    
                    i = -1 
                    
                self._track_vars(stmt)
                i += 1

            if first_func_modified is None:
                return [self.original_func]

            return [first_func_modified] + self.generated_functions

    def _track_vars(self, stmt):
        """Simple tracker for variables assigned on the Left Hand Side"""
        if isinstance(stmt, cst.SimpleStatementLine):
            for element in stmt.body:
                if isinstance(element, cst.Assign):
                    for target in element.targets:
                        if isinstance(target.target, cst.Name):
                            self.defined_vars.add(target.target.value)

    def _is_remote_call(self, stmt):
        """
        Determines if a statement is a synchronous remote call that needs splitting.
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
        if isinstance(base, cst.Name):
            var_name = base.value
            if var_name in self.local_types:
                var_type = self.local_types[var_name]
                if var_type in self.entities:
                    return True
        return False

    def _create_dispatch_block(self, obj, method, call_node, reply_to):
        args = call_node.args[0].value if call_node.args else cst.Name("None")

        op_name = self.entities.get(self.local_types.get(obj, ""), obj)
        reply_op_name = self.entities[self.class_name]

        # context dict
        context_entries = [
            cst.DictElement(
                key=cst.SimpleString(f"'{v}'"),
                value=cst.Name(v)
            )
            for v in self.defined_vars
        ]

        context_dict = cst.Dict(elements=context_entries)

        # reply_info dict
        reply_info_dict = cst.Dict(elements=[
            cst.DictElement(cst.SimpleString("'op_name'"), cst.SimpleString(f"'{reply_op_name}'")),
            cst.DictElement(cst.SimpleString("'fun'"), cst.SimpleString(f"'{reply_to}'")),
            cst.DictElement(cst.SimpleString("'id'"), cst.parse_expression(f"{obj}")),
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

        async_call = cst.SimpleStatementLine(
            body=[cst.Expr(
                value=cst.Call(
                    func=cst.parse_expression("ctx.call_remote_async"),
                    args=[
                        cst.Arg(keyword=cst.Name("operator_name"), value=cst.SimpleString(f"'{op_name}'")),
                        cst.Arg(keyword=cst.Name("function_name"), value=cst.SimpleString(f"'{method}'")),
                        cst.Arg(keyword=cst.Name("key"), value=cst.parse_expression(f"{obj}")),
                        cst.Arg(keyword=cst.Name("params"), value=args),
                    ]
                )
            )]
        )

        return [push_reply_info, async_call]

    def _create_restore_block(self):
        """Restore locals from params dict"""
        get_state = cst.parse_statement("state = ctx.get()")

        restore_statements = []

        for var in sorted(self.defined_vars):
            restore_statements.append(
                cst.SimpleStatementLine(
                    body=[
                        cst.Assign(
                            targets=[
                                cst.AssignTarget(
                                    target=cst.Name(var)
                                )
                            ],
                            value=cst.Subscript(
                                value=cst.Name("params"),
                                slice=[
                                    cst.SubscriptElement(
                                        slice=cst.Index(
                                            value=cst.SimpleString(f"'{var}'")
                                        )
                                    )
                                ]
                            )
                        )
                    ]
                )
            )

        return [get_state] + restore_statements

    # def _create_continuation(self, name, body, target_var):
    #     """Create a continuation function"""
    #     op_name = self.entities[self.class_name] + "_operator"
    #     deco = cst.Decorator(decorator=cst.parse_expression(f"{op_name}.register"))

    #     reply_to_param = cst.Param(
    #         name=cst.Name("reply_to"),
    #         annotation=cst.Annotation(annotation=cst.Name("list")),
    #         default=cst.Name("None"),
    #     )
        
    #     params_list = [
    #         cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction"))),
    #         cst.Param(name=cst.Name("params")),
    #     ]
        
    #     if target_var is not None:
    #         params_list.append(cst.Param(name=cst.Name(target_var)))
        
    #     params_list.append(reply_to_param)
        
    #     return cst.FunctionDef(
    #         name=cst.Name(name),
    #         params=cst.Parameters(params=params_list),
    #         body=cst.IndentedBlock(body=body),
    #         decorators=[deco],
    #         asynchronous=cst.Asynchronous()
    #     )

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

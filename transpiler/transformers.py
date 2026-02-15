"""
Transformer classes for the Styx transpiler.
"""

import libcst as cst
import libcst.matchers as m
from typing import List, Dict, Union, Tuple


class ReturnHandlerTransformer(cst.CSTTransformer):
    """
    Finds 'return' statements and wraps them with the reply_to stack logic.
    Also injects 'ctx.put(state)' immediately before the logic.
    """

    def __init__(self):
        self.state_dirty_stack = [False]

    def _mark_dirty(self):
        self.state_dirty_stack[-1] = True

    def _is_graph_terminal(self, node: Union[cst.CSTNode, None]) -> bool:
        """
        Recursively checks if a node guarantees an exit (return or raise).
        """
        if node is None:
            return False
            
        if isinstance(node, (cst.Return, cst.Raise, cst.Break, cst.Continue)):
            return True
            
        if isinstance(node, cst.SimpleStatementLine):
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
            elif isinstance(node.orelse, cst.If): # elif chain
                else_terminal = self._is_graph_terminal(node.orelse)
                
            return body_terminal and else_terminal

        return False

    def leave_Assign(self, original_node, updated_node):
        for target in updated_node.targets:
            if m.matches(
                target.target,
                m.Subscript(value=m.Name("state"))
            ):
                self._mark_dirty()
        return updated_node

    def leave_AugAssign(self, original_node, updated_node):
        if m.matches(
            updated_node.target,
            m.Subscript(value=m.Name("state"))
        ):
            self._mark_dirty()
        return updated_node

    def leave_SimpleStatementLine(self, original_node, updated_node):
        return_node = None
        for node in updated_node.body:
            if isinstance(node, cst.Return):
                return_node = node
                break
        
        if not return_node:
            return updated_node
            
        ret_val = return_node.value if return_node.value else cst.Name("None")

        pop_reply = cst.parse_statement("reply_info = reply_to.pop()")
        
        params_tuple = cst.Tuple(
            elements=[
                cst.Element(value=cst.parse_expression('reply_info["context"]')),
                cst.Element(value=ret_val),
                cst.Element(value=cst.Name("reply_to")),
            ]
        )

        call_remote = cst.Call(
            func=cst.parse_expression("ctx.call_remote_async"),
            args=[
                cst.Arg(keyword=cst.Name("operator_name"), value=cst.parse_expression('reply_info["op_name"]')),
                cst.Arg(keyword=cst.Name("function_name"), value=cst.parse_expression('reply_info["fun"]')),
                cst.Arg(keyword=cst.Name("key"), value=cst.parse_expression('reply_info["id"]')),
                cst.Arg(keyword=cst.Name("params"), value=params_tuple)
            ]
        )
        
        if_block = cst.IndentedBlock(
            body=[
                cst.SimpleStatementLine(body=[pop_reply.body[0]]),
                cst.SimpleStatementLine(body=[cst.Expr(value=call_remote)]),
                cst.SimpleStatementLine(body=[cst.Return(value=None)])
            ]
        )
        
        else_block = cst.Else(
            body=cst.IndentedBlock(
                body=[updated_node]
            )
        )
        
        if_stmt = cst.If(
            test=cst.Name("reply_to"),
            body=if_block,
            orelse=else_block
        )

        put_state = cst.parse_statement("ctx.put(state)")

        res = cst.FlattenSentinel([put_state, if_stmt]) if self.state_dirty_stack[-1] else if_stmt
        
        return res

    def leave_FunctionDef(self, original_node, updated_node):
        if self.state_dirty_stack[-1]:
            body_stmts = updated_node.body.body
            last_stmt = body_stmts[-1] if body_stmts else None

            if not self._is_graph_terminal(last_stmt):
                put_state = cst.parse_statement("ctx.put(state)")
                new_body = list(updated_node.body.body) + [put_state]
                
                return updated_node.with_changes(
                    body=updated_node.body.with_changes(body=new_body)
                )
        
        return updated_node

class RemoteCallLinearizer(cst.CSTTransformer):
    """
    Linearizes remote calls within functions.
    """
    
    def __init__(self):
        self.call_counter = 0
        
    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
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
        self.counter = 1
        
    def leave_SimpleStatementLine(self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine) -> Union[cst.SimpleStatementLine, cst.FlattenSentinel[cst.SimpleStatementLine]]:
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
                        
                elif isinstance(new_stmt, cst.Assign) and len(new_stmt.targets) == 1:
                    if isinstance(new_stmt.value, cst.Name) and new_stmt.value.value == last_extracted_var:
                        should_collapse = True

            if should_collapse:
                extractor.extracted_calls.pop()
                new_stmt = new_stmt.with_changes(value=last_extracted_call)
            else:
                self.counter = extractor.counter
            
            for var_name, call in extractor.extracted_calls:
                assignment = cst.SimpleStatementLine(
                    body=[cst.Assign(
                        targets=[cst.AssignTarget(target=cst.Name(var_name))],
                        value=call
                    )]
                )
                new_statements.append(assignment)
            
            new_statements.append(cst.SimpleStatementLine(body=[new_stmt]))
        
        return cst.FlattenSentinel(new_statements)
    
    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> Union[cst.If, cst.FlattenSentinel[cst.BaseStatement]]:
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
                body=[cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name(var_name))],
                    value=call
                )]
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
        self.extracted_calls: List[Tuple[str, cst.Call]] = []
        self.counter = start_counter
        
    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        if isinstance(updated_node.func, cst.Attribute):
            var_name = f"attr_{self.counter}"
            self.counter += 1
            
            self.extracted_calls.append((var_name, updated_node))
            
            return cst.Name(var_name)
        
        return updated_node


class InitBodyTransformer(cst.CSTTransformer):
    """
    Transforms __init__ method body to extract state dictionary entries.
    """
    
    def __init__(self):
        self.state_dict_entries = []
        self.other_statements = []

    def leave_SimpleStatementLine(self, original_node, updated_node):
        for stmt in original_node.body:
            if isinstance(stmt, cst.AnnAssign):
                if m.matches(stmt.target, m.Attribute(value=m.Name("self"))):
                    key = stmt.target.attr.value
                    value = stmt.value
                    
                    self.state_dict_entries.append(
                        cst.DictElement(key=cst.SimpleString(f"'{key}'"), value=value)
                    )
                    return cst.RemoveFromParent()
            elif isinstance(stmt, cst.Assign):
                if len(stmt.targets) == 1:
                    target = stmt.targets[0].target
                    if m.matches(target, m.Attribute(value=m.Name("self"))):
                        key = target.attr.value
                        value = stmt.value
                        
                        self.state_dict_entries.append(
                            cst.DictElement(key=cst.SimpleString(f"'{key}'"), value=value)
                        )
                        return cst.RemoveFromParent()
        
        self.other_statements.append(updated_node)
        return updated_node


class StateAccessTransformer(cst.CSTTransformer):
    """
    Transforms self.attribute access to state['attribute'] dictionary access.
    """
    
    def leave_Attribute(self, original_node, updated_node):
        if m.matches(original_node, m.Attribute(value=m.Name("self"))):
            return cst.Subscript(
                value=cst.Name("state"),
                slice=[
                    cst.SubscriptElement(slice=cst.Index(value=cst.SimpleString(f"'{original_node.attr.value}'")))
                ]
            )
        return updated_node
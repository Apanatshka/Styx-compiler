"""
Main Styx transpiler implementation.
"""

import libcst as cst
from typing import List, Dict, Union

from config import N_PARTITIONS
from visitor import EntityDiscoveryVisitor
from transformers import (
    ReturnHandlerTransformer,
    RemoteCallLinearizer,
    InitBodyTransformer,
    StateAccessTransformer,
)
from processor import FunctionProcessor


class StyxTransformer(cst.CSTTransformer):
    """
    Main transformer that processes entity classes and converts them to Styx operators.
    """
    
    def __init__(self, entities: Dict[str, str]):
        self.entities = entities
        self.current_operator = None
        self.self_attr_types: Dict[str, str] = {}

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if node.name.value in self.entities:
            self.current_operator = node.name.value
            self.self_attr_types = {}
            return True 
        return False 

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        imports = [
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.operator import Operator").body[0]]),
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.stateful_function import StatefulFunction").body[0]]),
            cst.EmptyLine()
        ]
        
        new_body = list(imports) + list(updated_node.body)
        return updated_node.with_changes(body=new_body)

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> Union[cst.ClassDef, cst.FlattenSentinel]:
        if original_node.name.value not in self.entities:
            return updated_node

        op_name = self.entities[original_node.name.value]
        
        op_def_code = f"{op_name}_operator = Operator('{op_name}', n_partitions={N_PARTITIONS})"
        op_def_node = cst.parse_statement(op_def_code)
        
        new_nodes = [op_def_node, cst.EmptyLine()]
        
        for statement in updated_node.body.body:
            if isinstance(statement, cst.FunctionDef):
                new_nodes.append(statement)
                new_nodes.append(cst.EmptyLine())
            elif isinstance(statement, cst.ClassDef):
                new_nodes.append(statement)
                new_nodes.append(cst.EmptyLine())
        
        return cst.FlattenSentinel(new_nodes)

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> Union[cst.FunctionDef, cst.FlattenSentinel]:
        func_name = original_node.name.value
        
        if func_name == "__init__":
            return self.transform_init(updated_node)
        elif func_name == "__key__":
            return cst.RemoveFromParent()
        else:
            return self.transform_method(updated_node)
        
    
    def _scan_init_for_attr_types(self, node: cst.FunctionDef):
        # Param name → type
        param_types = {}
        for p in node.params.params:
            if p.annotation and isinstance(p.annotation.annotation, cst.Name):
                param_types[p.name.value] = p.annotation.annotation.value

        for stmt in node.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue

            for element in stmt.body:

                # -------- AnnAssign (self.x: Type = ...)
                if isinstance(element, cst.AnnAssign):
                    target = element.target

                    if (
                        isinstance(target, cst.Attribute)
                        and isinstance(target.value, cst.Name)
                        and target.value.value == "self"
                    ):
                        attr_name = target.attr.value

                        if isinstance(element.annotation.annotation, cst.Name):
                            type_name = element.annotation.annotation.value
                            if type_name in self.entities:
                                self.self_attr_types[attr_name] = type_name

                # -------- Assign (self.x = ...)
                elif isinstance(element, cst.Assign):
                    target = element.targets[0].target
                    value = element.value

                    if (
                        isinstance(target, cst.Attribute)
                        and isinstance(target.value, cst.Name)
                        and target.value.value == "self"
                    ):
                        attr_name = target.attr.value

                        # self.x = param
                        if isinstance(value, cst.Name):
                            rhs_type = param_types.get(value.value)
                            if rhs_type in self.entities:
                                self.self_attr_types[attr_name] = rhs_type

                        # self.x = Entity(...)
                        elif isinstance(value, cst.Call) and isinstance(value.func, cst.Name):
                            if value.func.value in self.entities:
                                self.self_attr_types[attr_name] = value.func.value


    def transform_init(self, node: cst.FunctionDef) -> cst.FunctionDef:
        self._scan_init_for_attr_types(node)
        new_name = cst.Name(value="create") 
        
        ctx_param = cst.Param(
            name=cst.Name("ctx"),
            annotation=cst.Annotation(annotation=cst.Name("StatefulFunction"))
        )
        reply_to_param = cst.Param(
            name=cst.Name("reply_to"),
            annotation=cst.Annotation(annotation=cst.Name("list")),
            default=cst.Name("None"),
        )
        new_params = [ctx_param] + [p for p in node.params.params if p.name.value != 'self'] + [reply_to_param]

        body_transformer = InitBodyTransformer()
        new_body = node.body.visit(body_transformer)

        get_state = cst.parse_statement("state = ctx.get()")
        
        dict_node = cst.Dict(elements=body_transformer.state_dict_entries)
        
        put_call = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[
                        cst.AssignTarget(
                            target=cst.Name("state")
                        )
                    ],
                    value=dict_node
                )
            ]
        )

        put_state = cst.parse_statement("ctx.put(state)")
        
        return_stmt = cst.parse_statement("return ctx.key")
        
        new_block = new_body.with_changes(
            body=[get_state] + body_transformer.other_statements + [put_call, put_state, return_stmt]
        )
        reply_to_transformer = ReturnHandlerTransformer()
        final_block = new_block.visit(reply_to_transformer)

        decorator_name = f"{self.entities[self.current_operator]}_operator"
        decorator = cst.Decorator(
            decorator=cst.Attribute(
                value=cst.Name(decorator_name),
                attr=cst.Name("register")
            )
        )

        return node.with_changes(
            name=new_name,
            params=node.params.with_changes(params=new_params),
            body=final_block, 
            asynchronous=cst.Asynchronous(),
            decorators=[decorator],
        )

    def transform_method(self, node: cst.FunctionDef) -> Union[cst.FunctionDef, cst.FlattenSentinel]:
        # 1. Linearize
        linearizer = RemoteCallLinearizer()
        node = node.visit(linearizer)

        # 2. Process and Split
        processor = FunctionProcessor(node, self.current_operator, self.entities, self.self_attr_types)
        new_functions = processor.process()

        # 3. Post-Process
        final_nodes = []
        
        for func in new_functions:
            state_transformer = StateAccessTransformer()
            func = func.visit(state_transformer)
            
            if func.name.value == node.name.value:
                get_state = cst.parse_statement("state = ctx.get()")
                func = func.with_changes(body=cst.IndentedBlock(body=[get_state] + list(func.body.body)))
                
                reply_to_transformer = ReturnHandlerTransformer()
                func = func.visit(reply_to_transformer)
                
                func = self._finalize_original_signature(func)
            else:
                # Apply Return Handler to continuations
                reply_to_transformer = ReturnHandlerTransformer()
                func = func.visit(reply_to_transformer)

            final_nodes.append(func)
            final_nodes.append(cst.EmptyLine())

        return cst.FlattenSentinel(final_nodes)

    def _finalize_original_signature(self, node):
        ctx_param = cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction")))
        reply_to_param = cst.Param(name=cst.Name("reply_to"), annotation=cst.Annotation(cst.Name("list")), default=cst.Name("None"))
        
        new_params = [ctx_param] + [p for p in node.params.params if p.name.value != "self"] + [reply_to_param]
        
        op_name = self.entities[self.current_operator] + "_operator"
        decorator = cst.Decorator(decorator=cst.Attribute(value=cst.Name(op_name), attr=cst.Name("register")))
        
        return node.with_changes(
            params=node.params.with_changes(params=new_params),
            decorators=[decorator],
            asynchronous=cst.Asynchronous()
        )


class StyxTranspiler:
    """
    Main transpiler class that orchestrates the transformation process.
    """
    
    def __init__(self, source_code: str):
        self.source_code = source_code
        self.cst_tree = cst.parse_module(source_code)
        self.entities: Dict[str, str] = {}  

    def run(self) -> str:
        """
        Run the transpilation process.
        
        Returns:
            str: The transpiled code
        """
        print("--- Starting Transpilation ---")

        visitor = EntityDiscoveryVisitor()
        self.cst_tree.visit(visitor)
        self.entities = visitor.entities
        print(f"Discovered Entities: {self.entities}")

        transformer = StyxTransformer(self.entities)
        modified_tree = self.cst_tree.visit(transformer)

        return modified_tree.code


# Main execution
if __name__ == "__main__":
    file_name = "key_reference_simple_example.py"
    input_file = "./examples/original/" + file_name
    output_file = "./examples/compiled/" + file_name

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            code = f.read()
    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
        exit(1)

    transpiler = StyxTranspiler(code)
    output_code = transpiler.run()

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_code)
        
    print(f"Successfully transpiled '{input_file}' to '{output_file}'")
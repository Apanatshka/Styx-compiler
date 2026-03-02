"""
Visitor classes for the Styx transpiler.
"""

import libcst as cst
import libcst.matchers as m


class EntityDiscoveryVisitor(cst.CSTVisitor):
    """
    Discovers entity classes marked with @entity decorator.
    """
    
    def __init__(self):
        self.entities = {}
        self.entity_keys = {}
        self.entity_init_params = {}  # e.g. {"Item": {"item_name": "str", "price": "int"}}

    def visit_ClassDef(self, node: cst.ClassDef):
        for decorator in node.decorators:
            if m.matches(decorator, m.Decorator(decorator=m.Name("entity"))):
                class_name = node.name.value
                self.entities[class_name] = class_name.lower()
                
                for item in node.body.body:
                    # Find the __key__ method to identify the key field
                    if isinstance(item, cst.FunctionDef) and item.name.value == "__key__":
                        for stmt in item.body.body:
                            if m.matches(stmt, m.SimpleStatementLine(body=[m.Return(value=m.Attribute(attr=m.Name()))])):
                                key_attr = stmt.body[0].value.attr.value
                                self.entity_keys[class_name] = key_attr

                    # Find __init__ to record parameter names and types (excluding 'self')
                    if isinstance(item, cst.FunctionDef) and item.name.value == "__init__":
                        params = {}
                        for p in item.params.params:
                            if p.name.value != "self":
                                type_str = None
                                if p.annotation and isinstance(p.annotation.annotation, cst.Name):
                                    type_str = p.annotation.annotation.value
                                params[p.name.value] = type_str
                        self.entity_init_params[class_name] = params
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

    def visit_ClassDef(self, node: cst.ClassDef):
        for decorator in node.decorators:
            if m.matches(decorator, m.Decorator(decorator=m.Name("entity"))):
                class_name = node.name.value
                self.entities[class_name] = class_name.lower()
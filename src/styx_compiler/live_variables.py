from collections import defaultdict
from collections.abc import Callable

import libcst as cst
from libcst import matchers as m
from libcst.metadata import QualifiedName, QualifiedNameProvider, QualifiedNameSource

from styx_compiler.control_flow import Node
from styx_compiler.data_flow import DataflowProperty, MaySet
from styx_compiler.metadata_providers import IndexProvider


class CollectLiveVariablesTransferFunctions(cst.CSTVisitor):
    """
    Computes the live variable analysis transfer functions for control-flow graph nodes
    """

    METADATA_DEPENDENCIES = (IndexProvider, QualifiedNameProvider)

    def __init__(self):
        super().__init__()
        self.active = False
        self._tfs: dict[Node, Callable[[frozenset[str]], frozenset[str]]] = defaultdict(lambda: lambda x: x)

    def get_dataflow_property(self) -> DataflowProperty:
        return DataflowProperty(forward=False, initial=frozenset(), transfer_func=self._tfs, lattice=MaySet())

    def _get_lhs_names(self, target: cst.BaseExpression) -> list[str]:
        if m.matches(target, m.Attribute()):
            target: cst.Attribute = cst.ensure_type(target, cst.Attribute)
            name_origin: set[QualifiedName] = self.get_metadata(QualifiedNameProvider, target.attr)
            if len(name_origin) == 1:
                [qual_name] = name_origin
                if qual_name.source == QualifiedNameSource.LOCAL:
                    return [target.attr.value]
        if m.matches(target, m.Subscript()):
            target: cst.Subscript = cst.ensure_type(target, cst.Subscript)
            return self._get_lhs_names(target.value)
        if m.matches(target, m.StarredElement() | m.Element()):
            return self._get_lhs_names(target.value)
        if m.matches(target, m.Name()):
            target: cst.Name = cst.ensure_type(target, cst.Name)
            name_origin: set[QualifiedName] = self.get_metadata(QualifiedNameProvider, target)
            if len(name_origin) == 1:
                [qual_name] = name_origin
                if qual_name.source == QualifiedNameSource.LOCAL:
                    return [target.value]
        if m.matches(target, m.List() | m.Tuple()):
            return [name for el in target.elements for name in self._get_lhs_names(el)]
        return []

    def visit_Param(self, node: cst.Param) -> bool | None:
        if self.active:
            index = self.get_metadata(IndexProvider, node)
            self._tfs[Node(index, 0)] = lambda lives: lives.difference([node.name.value])
            return False
        return None

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
        if self.active:
            index = self.get_metadata(IndexProvider, node.target)
            names = self._get_lhs_names(node.target)
            self._tfs[Node(index, 0)] = lambda lives: lives.difference(names)

    def visit_Assign(self, node: cst.Assign) -> bool | None:
        if self.active:
            for target in node.targets:
                index = self.get_metadata(IndexProvider, target)
                names = self._get_lhs_names(target.target)
                self._tfs[Node(index, 0)] = lambda lives: lives.difference(names)

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        if self.active:
            index = self.get_metadata(IndexProvider, node)
            names = self._get_lhs_names(node.target)
            self._tfs[Node(index, 0)] = lambda lives: lives.difference(names)

    def visit_Name(self, node: cst.Name) -> bool | None:
        if self.active:
            name_origin: set[QualifiedName] = self.get_metadata(QualifiedNameProvider, node)
            if len(name_origin) == 1:
                [qual_name] = name_origin
                if qual_name.source == QualifiedNameSource.LOCAL:
                    index = self.get_metadata(IndexProvider, node)
                    self._tfs[Node(index, 0)] = lambda lives: lives.union([node.value])

    def visit_FunctionDef_params(self, _node: cst.FunctionDef) -> None:
        self.active = True

    def leave_FunctionDef_params(self, _node: cst.FunctionDef) -> None:
        self.active = False

    def visit_FunctionDef_body(self, _node: cst.FunctionDef) -> None:
        self.active = True

    def leave_FunctionDef_body(self, _node: cst.FunctionDef) -> None:
        self.active = False

    def visit_AnnAssign_annotation(self, _node: cst.FunctionDef) -> None:
        self.active = False

    def leave_AnnAssign_annotation(self, _node: cst.FunctionDef) -> None:
        self.active = True

    def visit_Attribute_attr(self, _node: cst.FunctionDef) -> None:
        self.active = False

    def leave_Attribute_attr(self, _node: cst.FunctionDef) -> None:
        self.active = True

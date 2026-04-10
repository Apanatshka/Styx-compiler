from collections import defaultdict
from collections.abc import Callable

import libcst as cst
from libcst import matchers as m
from libcst.metadata import QualifiedName, QualifiedNameProvider, QualifiedNameSource

from styx_compiler.control_flow import ControlFlowGraphProvider, Node
from styx_compiler.data_flow import TB, DataflowProperty, MaySet, compute_dataflow_property
from styx_compiler.metadata_providers import IndexProvider


class CollectLiveVariablesTransferFunctions(cst.CSTVisitor):
    """
    Computes the live variable analysis transfer functions for control-flow graph nodes
    """

    def __init__(self, provider: LiveVariablesDataflowPropertyProvider):
        super().__init__()
        self._provider = provider
        self._active = False
        self._tfs: dict[Node, Callable[[frozenset[str]], frozenset[str]]] = defaultdict(lambda: lambda x: x)

    def get_dataflow_property(self) -> DataflowProperty:
        return DataflowProperty(forward=False, initial=frozenset(), transfer_func=self._tfs, lattice=MaySet())

    def _get_lhs_names(self, target: cst.BaseExpression) -> list[str]:
        if m.matches(target, m.Attribute()):
            target: cst.Attribute = cst.ensure_type(target, cst.Attribute)
            name_origin: set[QualifiedName] = self._provider.get_metadata(QualifiedNameProvider, target.attr)
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
            name_origin: set[QualifiedName] = self._provider.get_metadata(QualifiedNameProvider, target)
            if len(name_origin) == 1:
                [qual_name] = name_origin
                if qual_name.source == QualifiedNameSource.LOCAL:
                    return [target.value]
        if m.matches(target, m.List() | m.Tuple()):
            # noinspection PyUnresolvedReferences
            return [name for el in target.elements for name in self._get_lhs_names(el)]
        return []

    def leave_Module(self, module: cst.Module) -> None:
        self._provider.set_metadata(module, self.get_dataflow_property())

    def visit_Param(self, node: cst.Param) -> bool | None:
        if self._active:
            index = self._provider.get_metadata(IndexProvider, node)
            name = node.name.value
            self._tfs[Node(index, 0)] = lambda lives, name=name: lives.difference([name])
            return False
        return None

    # noinspection PyDefaultArgument
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
        if self._active:
            index = self._provider.get_metadata(IndexProvider, node.target)
            names = self._get_lhs_names(node.target)
            self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    # noinspection PyDefaultArgument
    def visit_Assign(self, node: cst.Assign) -> bool | None:
        if self._active:
            for target in node.targets:
                index = self._provider.get_metadata(IndexProvider, target)
                names = self._get_lhs_names(target.target)
                self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    # noinspection PyDefaultArgument
    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        if self._active:
            index = self._provider.get_metadata(IndexProvider, node)
            names = self._get_lhs_names(node.target)
            self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_Name(self, node: cst.Name) -> bool | None:
        if self._active:
            name_origin: set[QualifiedName] = self._provider.get_metadata(QualifiedNameProvider, node)
            if len(name_origin) == 1:
                [qual_name] = name_origin
                if qual_name.source == QualifiedNameSource.LOCAL:
                    index = self._provider.get_metadata(IndexProvider, node)
                    name = node.value
                    self._tfs[Node(index, 0)] = lambda lives, name=name: lives.union([name])

    def visit_FunctionDef_params(self, _node: cst.FunctionDef) -> None:
        self._active = True

    def leave_FunctionDef_params(self, _node: cst.FunctionDef) -> None:
        self._active = False

    def visit_FunctionDef_body(self, _node: cst.FunctionDef) -> None:
        self._active = True

    def leave_FunctionDef_body(self, _node: cst.FunctionDef) -> None:
        self._active = False

    def visit_AnnAssign_annotation(self, _node: cst.FunctionDef) -> None:
        self._active = False

    def leave_AnnAssign_annotation(self, _node: cst.FunctionDef) -> None:
        self._active = True

    def visit_Attribute_attr(self, _node: cst.FunctionDef) -> None:
        self._active = False

    def leave_Attribute_attr(self, _node: cst.FunctionDef) -> None:
        self._active = True


class LiveVariablesDataflowPropertyProvider(cst.BatchableMetadataProvider[DataflowProperty]):
    METADATA_DEPENDENCIES = (IndexProvider, QualifiedNameProvider)

    def visit_Module(self, module: cst.Module) -> None:
        module.visit(CollectLiveVariablesTransferFunctions(self))


class LiveVariablesVisitor(cst.CSTVisitor):
    def __init__(
        self, provider: LiveVariablesProvider, live_vars: dict[Node, tuple[TB[frozenset[str]], TB[frozenset[str]]]]
    ):
        super().__init__()
        self._provider: LiveVariablesProvider = provider
        self.live_vars: dict[Node, tuple[TB[frozenset[str]], TB[frozenset[str]]]] = live_vars

    def on_visit(self, node: cst.CSTNode) -> bool:
        if m.matches(node, m.SimpleWhitespace() | m.TrailingWhitespace()):
            return False
        if self._provider.get_metadata(IndexProvider, node, None) is not None:
            cfg_node = Node(self._provider.get_metadata(IndexProvider, node), 0)
            if cfg_node in self.live_vars:
                self._provider.set_metadata(node, self.live_vars[cfg_node])
        return True


class LiveVariablesProvider(cst.BatchableMetadataProvider[tuple[frozenset[TB[str]], frozenset[TB[str]]]]):
    METADATA_DEPENDENCIES = (IndexProvider, ControlFlowGraphProvider, LiveVariablesDataflowPropertyProvider)

    def visit_Module(self, node: cst.Module) -> bool | None:
        cfg, start_end = self.get_metadata(ControlFlowGraphProvider, node)
        lv_prop = self.get_metadata(LiveVariablesDataflowPropertyProvider, node)
        lv_result = compute_dataflow_property(cfg, start_end, lv_prop)
        node.visit(LiveVariablesVisitor(self, lv_result))

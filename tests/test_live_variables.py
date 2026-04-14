"""Tests for styx_compiler.live_variables and indirectly for styx_compiler.data_flow."""

import libcst as cst
from libcst.metadata import QualifiedName, QualifiedNameSource

from styx_compiler.control_flow import ControlFlowGraphProvider, Node
from styx_compiler.live_variables import (
    LiveVariablesProvider,
)
from styx_compiler.metadata_providers import IndexProvider
from tests.test_cfg import add_fundef, loop_test

# def test_add_fundef_live_vars():
#     source_tree = cst.parse_module(add_fundef)
#     wrapper = cst.MetadataWrapper(source_tree)
#     cfgp = ControlFlowGraphProvider()
#     ccfg = ComputeControlFlowGraph(cfgp)
#     wrapper.visit(ccfg)
#     lvdpp = LiveVariablesDataflowPropertyProvider()
#     clvtf = CollectLiveVariablesTransferFunctions(lvdpp)
#     wrapper.visit(clvtf)
#     lv_prop = clvtf.get_dataflow_property()
#     lv_result = compute_dataflow_property(ccfg._cfg, ccfg._start_end, lv_prop)
#     print(lv_result)
#
#
# def test_user_item_live_vars():
#     source_tree = cst.parse_module(user_item)
#     wrapper = cst.MetadataWrapper(source_tree)
#     cfgp = ControlFlowGraphProvider()
#     ccfg = ComputeControlFlowGraph(cfgp)
#     wrapper.visit(ccfg)
#     lvdpp = LiveVariablesDataflowPropertyProvider()
#     clvtf = CollectLiveVariablesTransferFunctions(lvdpp)
#     wrapper.visit(clvtf)
#     lv_prop = clvtf.get_dataflow_property()
#     lv_result = compute_dataflow_property(ccfg._cfg, ccfg._start_end, lv_prop)
#     print(lv_result)


def test_add_fundef_live_vars_provider():
    source_tree = cst.parse_module(add_fundef)
    wrapper = cst.MetadataWrapper(source_tree)
    lvt1 = LiveVariablesTester1()
    wrapper.visit(lvt1)


class LiveVariablesTester1(cst.CSTVisitor):
    """
    Checks that each kind of CST Node that should have a corresponding CFG node has one
    """

    METADATA_DEPENDENCIES = (LiveVariablesProvider,)

    @staticmethod
    def local(name: str) -> QualifiedName:
        return QualifiedName(name=f"add.<locals>.{name}", source=QualifiedNameSource.LOCAL)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        empty = frozenset()
        ab = frozenset([self.local("a"), self.local("b")])
        a = frozenset([self.local("a")])
        assert self.get_metadata(LiveVariablesProvider, node.params.params[1], None) == (a, ab)
        assert self.get_metadata(LiveVariablesProvider, node.params.params[0], None) == (empty, a)
        assert self.get_metadata(LiveVariablesProvider, node, None) == (empty, empty)


def test_loop_test_live_vars_provider():
    source_tree = cst.parse_module(loop_test)
    wrapper = cst.MetadataWrapper(source_tree)
    lvt1 = LiveVariablesTester2()
    wrapper.visit(lvt1)


class LiveVariablesTester2(cst.CSTVisitor):
    """
    Checks that each kind of CST Node that should have a corresponding CFG node has one
    """

    METADATA_DEPENDENCIES = (LiveVariablesProvider, ControlFlowGraphProvider, IndexProvider)

    def __init__(self):
        super().__init__()
        self._cfg = None

    def is_edge(self, from_node: cst.CSTNode, to_node: cst.CSTNode) -> bool:
        from_idx: int | None = self.get_metadata(IndexProvider, from_node, None)
        if from_idx is None:
            return False
        from_cfg = Node(from_idx, 0)
        if from_cfg not in self._cfg:
            return False
        to_idx: int | None = self.get_metadata(IndexProvider, to_node, None)
        if to_idx is None:
            return False
        to_cfg = Node(to_idx, 0)
        return to_cfg in self._cfg[from_cfg]

    @staticmethod
    def local(name: str) -> QualifiedName:
        return QualifiedName(name=f"Something.loop_test.<locals>.{name}", source=QualifiedNameSource.LOCAL)

    def visit_Module(self, node: cst.Module) -> bool | None:
        self._cfg, _ = self.get_metadata(ControlFlowGraphProvider, node)

    def visit_Call(self, node: cst.Call) -> bool | None:
        assert self.get_metadata(LiveVariablesProvider, node, None)[1] == frozenset(
            [self.local("val"), self.local("item.get_price"), self.local("cart")]
        )

    # def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
    #     print(node)
    #     assert self.is_edge(node.target, node.value), (
    #         "AugAssign target should have an edge to AugAssign value is both are cst.Name"
    #     )
    #     print(self.get_metadata(LiveVariablesProvider, node.target, None))
    #     print(self.get_metadata(LiveVariablesProvider, node.value, None))
    #     print(self.get_metadata(LiveVariablesProvider, node, None))
    #
    # def visit_Assign(self, node: cst.Assign) -> bool | None:
    #     print(node)
    #     print(self.get_metadata(LiveVariablesProvider, node.value, None))
    #     for target in node.targets:
    #         print(self.get_metadata(LiveVariablesProvider, target, None))
    #
    # def visit_Return(self, node: cst.Return) -> bool | None:
    #     print(node)
    #     print(self.get_metadata(LiveVariablesProvider, node.value, None))

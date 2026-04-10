"""Tests for styx_compiler.live_variables and indirectly for styx_compiler.data_flow."""

import libcst as cst

from styx_compiler.live_variables import (
    LiveVariablesProvider,
)
from tests.test_cfg import add_fundef

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

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        assert self.get_metadata(LiveVariablesProvider, node, None) == (frozenset(), frozenset())

"""Tests for styx_compiler.live_variables and indirectly for styx_compiler.data_flow."""

import libcst as cst
import libcst.matchers as m
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
            [self.local("val"), self.local("cart")]
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


subscript_assign = """
def subscript_assign(lst, i, val):
    lst[i] = val
    return lst
"""


def test_subscript_assign_keeps_list_live():
    """lst[i] = val must not kill lst — lst is mutated, not redefined."""
    source_tree = cst.parse_module(subscript_assign)
    wrapper = cst.MetadataWrapper(source_tree)
    wrapper.visit(SubscriptAssignTester())


class SubscriptAssignTester(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (LiveVariablesProvider,)

    @staticmethod
    def local(name: str) -> QualifiedName:
        return QualifiedName(name=f"subscript_assign.<locals>.{name}", source=QualifiedNameSource.LOCAL)

    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool | None:
        live_in, live_out = self.get_metadata(LiveVariablesProvider, node)
        assert self.local("lst") in live_out
        assert self.local("lst") in live_in


aug_subscript = """
def aug_subscript(lst, i):
    lst[i] += 1
    return lst
"""


def test_aug_subscript_keeps_list_live():
    source_tree = cst.parse_module(aug_subscript)
    wrapper = cst.MetadataWrapper(source_tree)
    wrapper.visit(AugSubscriptTester())


class AugSubscriptTester(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (LiveVariablesProvider,)

    @staticmethod
    def local(name: str) -> QualifiedName:
        return QualifiedName(name=f"aug_subscript.<locals>.{name}", source=QualifiedNameSource.LOCAL)

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        live_in, live_out = self.get_metadata(LiveVariablesProvider, node)
        assert self.local("lst") in live_out
        assert self.local("lst") in live_in


method_call_source = """
def method_call_func(items, item):
    items.append(item)
    return items
"""


def test_method_call_does_not_add_method_name():
    """items.append(item) — 'append' is a method, not a live variable."""
    source_tree = cst.parse_module(method_call_source)
    wrapper = cst.MetadataWrapper(source_tree)
    wrapper.visit(MethodCallLiveVarsTester())


class MethodCallLiveVarsTester(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (LiveVariablesProvider,)

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        metadata = self.get_metadata(LiveVariablesProvider, node, None)
        if metadata is None:
            return None
        live_in, _ = metadata
        items_append = QualifiedName(
            name="method_call_func.<locals>.items.append",
            source=QualifiedNameSource.LOCAL,
        )
        assert items_append not in live_in


# Advanced test: nested loops with an if condition.
# Expected live sets computed by hand via backward fixed-point analysis:
#
#   Lout (live at outer-loop header xs Name) = {xs, ys, limit, total}
#   Lin  (live at inner-loop header ys Name) = {xs, ys, x, limit, total}
#
# Outer For node kills x  → live_in misses x, live_out has it.
# Inner For node kills y  → live_in misses y, live_out has it.
# ComparisonTarget        → all variables x, y, xs, ys, limit, total live.
# AugAssign kills total   → live_in misses total, live_out has it (needed by
#                           the next iteration and the return).

nested_loops_if = """
def nested_loops_if(xs, ys, limit):
    total = 0
    for x in xs:
        for y in ys:
            if x < limit:
                total += y
    return total
"""


def test_nested_loops_if_live_vars():
    source_tree = cst.parse_module(nested_loops_if)
    wrapper = cst.MetadataWrapper(source_tree)
    wrapper.visit(NestedLoopsIfTester())


class NestedLoopsIfTester(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (LiveVariablesProvider,)

    @staticmethod
    def local(name: str) -> QualifiedName:
        return QualifiedName(name=f"nested_loops_if.<locals>.{name}", source=QualifiedNameSource.LOCAL)

    def visit_For(self, node: cst.For) -> bool | None:
        live_in, live_out = self.get_metadata(LiveVariablesProvider, node)
        xs, ys, x, y, limit, total = (self.local(n) for n in ("xs", "ys", "x", "y", "limit", "total"))

        if m.matches(node.iter, m.Name(value="xs")):
            assert live_in == frozenset([xs, ys, limit, total])
            assert live_out == frozenset([xs, ys, x, limit, total])

        elif m.matches(node.iter, m.Name(value="ys")):
            assert live_in == frozenset([xs, ys, x, limit, total])
            assert live_out == frozenset([xs, ys, x, y, limit, total])

    def visit_ComparisonTarget(self, node: cst.ComparisonTarget) -> bool | None:
        live_in, live_out = self.get_metadata(LiveVariablesProvider, node)
        expected = frozenset(self.local(n) for n in ("xs", "ys", "x", "y", "limit", "total"))
        assert live_in == expected
        assert live_out == expected

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        live_in, live_out = self.get_metadata(LiveVariablesProvider, node)
        xs, ys, x, limit, total = (self.local(n) for n in ("xs", "ys", "x", "limit", "total"))
        assert live_out == frozenset([xs, ys, x, limit, total])
        assert live_in == frozenset([xs, ys, x, limit])

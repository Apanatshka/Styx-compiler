"""Tests for styx_compiler.control_flow."""

import libcst as cst
import libcst.matchers as m

from styx_compiler.control_flow import CfgNode, ControlFlowGraphProvider, Node
from styx_compiler.metadata_providers import IndexProvider

add_fundef = """
def add(a: int, b: int) -> int:
    return a + b
"""


user_item = """
@entity
class Item:
    def __init__(self, item_name: str, price: int):
        self.item_name: str = item_name
        self.stock: int = 0
        self.price: int = price

    def get_price(self) -> int:
        return self.price

    def get_stock(self) -> int:
        return self.stock

    def update_stock(self, amount: int) -> bool:
        if (self.stock + amount) < 0:
            raise OutOfStock("Not enough stock to update.")

        self.stock += amount
        return True

    def __key__(self):
        return self.item_name
"""


nested_try = """
def nested_try(a: int) -> int:
    try:
        if a < 0:
            return 0
        elif a > 10:
            raise RuntimeError
        elif a == 10:
            raise OutOfStock("Not enough stock to update.")
        a += 1
    except OutOfStock:
        if a < 0:
            return 0
        elif a > 10:
            raise RuntimeError
        a += 1
    else:
        if a > 10:
            raise OutOfStock("Not enough stock to update.")
        a += 1
    finally:
        if a < 10:
            return 9001
    return a + 42
"""


def test_node_existence():
    node_existence(add_fundef)
    node_existence(user_item)
    node_existence(nested_try)


def node_existence(source_string: str):
    source_tree = cst.parse_module(source_string)
    wrapper = cst.MetadataWrapper(source_tree)
    cnt = CfgNodeTester()
    wrapper.visit(cnt)


class CfgNodeTester(cst.CSTVisitor):
    """
    Checks that each kind of CST Node that should have a corresponding CFG node has one
    """

    METADATA_DEPENDENCIES = (IndexProvider, ControlFlowGraphProvider)

    def __init__(self):
        super().__init__()
        self.cfg = None
        self.active = False

    def visit_Module(self, node: cst.Module) -> bool | None:
        self.cfg, _start_end = self.get_metadata(ControlFlowGraphProvider, node)

    def _has_node(self, node: cst.CSTNode, instance: int = 0) -> bool:
        """
        Tests if the CSTNode has a corresponding CFG node with outgoing edges
        """
        n = Node(self.get_metadata(IndexProvider, node), instance)
        return n in self.cfg

    def visit_Param(self, node: cst.Param) -> bool | None:
        if self.active:
            assert self._has_node(node)
            return False
        return None

    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_NameItem(self, node: cst.NameItem) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Attribute(self, node: cst.Attribute) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Name(self, node: cst.Name) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_AsName(self, node: cst.AsName) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_UnaryOperation(self, node: cst.UnaryOperation) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_BooleanOperation(self, node: cst.BooleanOperation) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_ComparisonTarget(self, node: cst.ComparisonTarget) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Await(self, node: cst.Await) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Yield(self, node: cst.Yield) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_From(self, node: cst.From) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Integer(self, node: cst.Integer) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Float(self, node: cst.Float) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Imaginary(self, node: cst.Imaginary) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_SimpleString(self, node: cst.SimpleString) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_ConcatenatedString(self, node: cst.ConcatenatedString) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_FormattedStringExpression(self, node: cst.FormattedStringExpression) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_FormattedString(self, node: cst.FormattedString) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Tuple(self, node: cst.Tuple) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_List(self, node: cst.List) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Set(self, node: cst.Set) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Element(self, node: cst.Element) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_StarredElement(self, node: cst.StarredElement) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_DictElement(self, node: cst.DictElement) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_StarredDictElement(self, node: cst.StarredDictElement) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_GeneratorExp(self, node: cst.GeneratorExp) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_ListComp(self, node: cst.ListComp) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_SetComp(self, node: cst.SetComp) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_DictComp(self, node: cst.DictComp) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Index(self, node: cst.Index) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Slice(self, node: cst.Slice) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_Subscript(self, node: cst.Subscript) -> bool | None:
        if self.active:
            assert self._has_node(node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        assert self._has_node(node)
        # We're not testing for instance 1, which is a final node and will not have outgoing edges

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


loop_test = """
@entity
class Something:
    def loop_test(self, cart: list[Item]) -> int:
        val = 0

        for item in cart:
            attr_1 = item.get_price()
            val += attr_1

        temp = 3

        val += temp

        return val
"""


def test_loop_test_cfg():
    module = cst.parse_module(loop_test)
    wrapper = cst.MetadataWrapper(module)
    ltct = LoopTestCfgTester()
    wrapper.visit(ltct)


class LoopTestCfgTester(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (IndexProvider, ControlFlowGraphProvider)

    def __init__(self):
        super().__init__()
        self.cfg: dict[CfgNode, set[CfgNode]] | None = None
        self.start_end: list[tuple[CfgNode, CfgNode]] | None = None

    def is_edge(self, from_node: CfgNode, to_node: CfgNode) -> bool:
        return from_node in self.cfg and to_node in self.cfg[from_node]

    def assert_node(self, node: cst.CSTNode, prev: CfgNode) -> CfgNode:
        cfg_node = Node(self.get_metadata(IndexProvider, node), 0)
        assert cfg_node in self.cfg
        assert self.is_edge(prev, cfg_node)
        return cfg_node

    def visit_Module(self, node: cst.Module) -> bool | None:
        self.cfg, self.start_end = self.get_metadata(ControlFlowGraphProvider, node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        assert node.name.value == "loop_test"

        # unpack

        init_val: cst.Assign
        for_loop: cst.For
        init_temp: cst.Assign
        update_val: cst.AugAssign
        return_val: cst.Return

        init_val, for_loop, init_temp, update_val, return_val = (
            cst.ensure_type(ssl, cst.SimpleStatementLine).body[0] if m.matches(ssl, m.SimpleStatementLine()) else ssl
            for ssl in node.body.body
        )

        for_iter_cart: cst.Name = cst.ensure_type(for_loop.iter, cst.Name)

        for_init_attr_1: cst.Assign
        for_update_val: cst.AugAssign

        for_init_attr_1, for_update_val = (
            cst.ensure_type(ssl, cst.SimpleStatementLine).body[0] for ssl in for_loop.body.body
        )

        for_init_attr_1_call: cst.Call = cst.ensure_type(for_init_attr_1.value, cst.Call)
        for_init_attr_1_call_expr: cst.Attribute = cst.ensure_type(for_init_attr_1_call.func, cst.Attribute)
        for_init_attr_1_call_item: cst.Name = cst.ensure_type(for_init_attr_1_call_expr.value, cst.Name)

        # FunctionDef

        index = self.get_metadata(IndexProvider, node)
        start = Node(index, 0)
        end = Node(index, 1)
        assert start in self.cfg

        assert (start, end) in self.start_end

        param_names = ["self", "cart"]
        prev = start
        for param, param_name in zip(node.params.params, param_names, strict=True):
            assert param.name.value == param_name
            prev = self.assert_node(param, prev)

        # FunctionDef Assign

        prev = self.assert_node(init_val.value, prev)
        prev = self.assert_node(init_val.targets[0], prev)

        # FunctionDef For

        prev = self.assert_node(for_iter_cart, prev)
        prev = self.assert_node(for_loop, prev)

        # FunctionDef For Assign Call Attribute

        prev = self.assert_node(for_init_attr_1_call_item, prev)
        prev = self.assert_node(for_init_attr_1_call_expr, prev)

        # FunctionDef For Assign Call

        prev = self.assert_node(for_init_attr_1_call, prev)

        # FunctionDef For Assign

        prev = self.assert_node(for_init_attr_1.targets[0], prev)

        # FunctionDef For AugAssign

        prev = self.assert_node(for_update_val.target, prev)
        prev = self.assert_node(for_update_val.value, prev)
        prev = self.assert_node(for_update_val, prev)

        # FunctionDef For (back-edge)

        assert self.is_edge(prev, Node(self.get_metadata(IndexProvider, for_iter_cart), 0))

        # FunctionDef Assign

        prev = self.assert_node(init_temp.value, prev)
        prev = self.assert_node(init_temp.targets[0], prev)

        # FunctionDef AugAssign

        prev = self.assert_node(update_val.target, prev)
        prev = self.assert_node(update_val.value, prev)
        prev = self.assert_node(update_val, prev)

        # FunctionDef Return

        prev = self.assert_node(return_val.value, prev)
        assert self.is_edge(prev, end)

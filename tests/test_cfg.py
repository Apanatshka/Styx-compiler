"""Tests for the styx_compiler.main."""

import libcst as cst

from styx_compiler.control_flow import ComputeControlFlowGraph

add_fundef = """
def add(a: int, b: int) -> int:
    return a + b
"""


def test_add_fundef_cfg():
    source_tree = cst.parse_module(add_fundef)
    wrapper = cst.MetadataWrapper(source_tree)
    ccfg = ComputeControlFlowGraph()
    assert len(ccfg._cfg) == 0
    wrapper.visit(ccfg)
    print(ccfg._cfg)
    assert len(ccfg._cfg) > 0
    assert len(ccfg._start_end) == 1


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


def test_multi_def_cfg():
    source_tree = cst.parse_module(user_item)
    wrapper = cst.MetadataWrapper(source_tree)
    ccfg = ComputeControlFlowGraph()
    wrapper.visit(ccfg)
    print(ccfg._cfg)
    assert len(ccfg._start_end) == 5

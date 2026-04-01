"""
Data-flow analysis engine as described in "FlowSpec: A declarative specification language for intra-procedural
flow-sensitive data-flow analysis" by Smits, Wachsmuth and Visser (https://doi.org/10.1016/j.cola.2019.100924).
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from styx_compiler.control_flow import Node

type Cfg = dict[Node, set[Node]]


def compute_sccs(cfg: Cfg, extremals: list[Node]) -> list[list[Node]]:
    """
    Tarjan's Strongly Connected Component algorithm, with a slight modification to force the order of nodes within an
     SCC into a postorder traversal. See section 5.3.1 / figure 26.

    You can reverse the control-flow graphs and give the list of end nodes and this will work just as well.

    Parameters:
        cfg (Cfg): one or more control-flow graphs
        extremals (list[Node]): a list of start nodes to the control-flow graphs

    Returns:
        list[list[Node]]: list of SCCs in topological order (use as a stack for topo order), where each SCC
          in reverse postorder over the depth-first spanning tree of the SCC.
    """
    index = 0
    scc_stack = []
    result = []
    node_index = {}
    node_lowlink = {}
    node_on_stack = set()

    def strong_connect(node: Node):
        nonlocal index, scc_stack, result, node_index, node_lowlink, node_on_stack
        node_index[node] = index
        node_lowlink[node] = index
        index += 1
        node_on_stack.add(node)
        # N.B. we don't add node to scc_stack here, only to the node_on_stack set. The set is used next and in the recursive
        #  calls, so it doesn't affect the algorithm's correctness to postpone adding to scc_stack.

        for next_node in cfg.get(node, set()):
            if next_node not in node_index:
                strong_connect(next_node)
                node_lowlink[node] = min(node_lowlink[node], node_lowlink[next_node])
            elif next_node in node_on_stack:
                node_lowlink[node] = min(node_lowlink[node], node_index[next_node])

        # Now we add the node to scc_stack in postorder
        scc_stack.append(node)

        if node_lowlink[node] == node_index[node]:
            scc = [scc_stack.pop()]
            node_on_stack.remove(scc[-1])
            while scc[-1] != node:
                scc.append(scc_stack.pop())
                node_on_stack.remove(scc[-1])
            result.append(list(reversed(scc)))

    for ext in extremals:
        if ext not in node_index:
            strong_connect(ext)

    return list(reversed(result))


@dataclass(frozen=True)
class SymbolicTop:
    pass


@dataclass(frozen=True)
class SymbolicBottom:
    pass


type TB[T] = T | SymbolicTop | SymbolicBottom


class Lattice[T](ABC):
    def __init__(self):
        super().__init__()
        self.top = SymbolicTop()
        self.bottom = SymbolicBottom()

    def nleq(self, left: TB[T], right: TB[T]) -> bool:
        if isinstance(left, SymbolicBottom) or isinstance(right, SymbolicTop):
            return False
        if isinstance(left, SymbolicTop) or isinstance(right, SymbolicBottom):
            return True
        return self._nleq_helper(left, right)

    @abstractmethod
    def _nleq_helper(self, left: T, right: T) -> bool:
        raise NotImplementedError()

    def join(self, left: TB[T], right: TB[T]) -> TB[T]:
        if isinstance(left, SymbolicTop) or isinstance(right, SymbolicTop):
            return SymbolicTop()
        if isinstance(left, SymbolicBottom):
            return right
        if isinstance(right, SymbolicBottom):
            return left
        return self._join_helper(left, right)

    @abstractmethod
    def _join_helper(self, left: T, right: T) -> T:
        raise NotImplementedError()


@dataclass(frozen=True)
class DataflowProperty[T]:
    forward: bool
    initial: T
    transfer_func: dict[Node, Callable[[T], T]]
    lattice: Lattice[T]


def compute_dataflow_property[T](
    cfg: Cfg,
    start_end: list[tuple[Node, Node]],
    df_property: DataflowProperty[T],
) -> dict[Node, tuple[TB[T], TB[T]]]:
    """
    Compute a single dataflow property. We're not doing dependent dataflow properties like the paper. See section 5.3.2
     and figure 27.
    TODO: filter the CFG to efficiently handle all the identity function transfer functions.
    """
    prop = {}
    for node, nexts in cfg.items():
        prop[node] = df_property.lattice.bottom
        for next_node in nexts:
            prop[next_node] = df_property.lattice.bottom

    if df_property.forward:
        extremals = [start for start, _ in start_end]
    else:
        rev_cfg = {}
        for node, nexts in cfg.items():
            for next_node in nexts:
                rev_cfg.setdefault(next_node, set()).add(node)
        cfg = rev_cfg
        extremals = [end for _, end in start_end]

    for node in extremals:
        prop[node] = df_property.initial

    sccs = compute_sccs(cfg, extremals)

    for scc in sccs:
        done = False
        while not done:
            done = True
            for node in scc:
                for next_node in cfg.get(node, set()):
                    assert not isinstance(prop[node], SymbolicTop | SymbolicBottom)
                    step = df_property.transfer_func[node](prop[node])
                    if df_property.lattice.nleq(step, prop[next_node]):
                        prop[next_node] = df_property.lattice.join(step, prop[next_node])
                        if next_node in scc:
                            done = False

    if df_property.forward:
        return {node: (p, df_property.transfer_func[node](p)) for node, p in prop.items()}
    return {node: (df_property.transfer_func[node](p), p) for node, p in prop.items()}


class MaySet[T](Lattice[frozenset[T]]):
    def __init__(self):
        super().__init__()
        self.bottom = frozenset()

    def _nleq_helper(self, left: frozenset[T], right: frozenset[T]) -> bool:
        return not (left <= right)

    def _join_helper(self, left: frozenset[T], right: frozenset[T]) -> frozenset[T]:
        return left | right


class MustSet[T](Lattice[frozenset[T]]):
    def __init__(self):
        super().__init__()
        self.top = frozenset()

    def _nleq_helper(self, left: frozenset[T], right: frozenset[T]) -> bool:
        return not (left >= right)

    def _join_helper(self, left: frozenset[T], right: frozenset[T]) -> frozenset[T]:
        return left & right

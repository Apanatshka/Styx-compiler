from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from styx_compiler.control_flow import Node

type Cfg = dict[Node, set[Node]]


def compute_sccs(cfg: Cfg, extremals: list[Node]) -> list[list[Node]]:
    """
    Tarjan's Strongly Connected Component algorithm, with a slight modification to force the order of nodes within an
     SCC into a postorder traversal. See https://doi.org/10.1016/j.cola.2019.100924, Section 5.3.1 / Figure 26.

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

        for next_node in cfg[node]:
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
    top = SymbolicTop()
    bottom = SymbolicBottom()

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
    transfer_func: Callable[[Node, TB[T]], TB[T]]
    lattice: Lattice[T]


def compute_dataflow_property[T](
    cfg: Cfg,
    start_end: list[tuple[Node, Node]],
    df_property: DataflowProperty[T],
) -> dict[Node, tuple[TB[T], TB[T]]]:
    prop = {}
    for node, nexts in cfg:
        prop[node] = df_property.lattice.bottom
        for next_node in nexts:
            prop[next_node] = df_property.lattice.bottom

    if df_property.forward:
        extremals = [start for start, _ in start_end]
    else:
        rev_cfg = {}
        for node, nexts in cfg:
            for next_node in nexts:
                rev_cfg[next_node] = cfg[node]
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
                for next_node in cfg[node]:
                    step = df_property.transfer_func(node, prop[node])
                    if df_property.lattice.nleq(step, prop[next_node]):
                        prop[next_node] = df_property.lattice.join(step, prop[next_node])
                        if next_node in scc:
                            done = False

    return {node: (p, df_property.transfer_func(node, p)) for node, p in prop}

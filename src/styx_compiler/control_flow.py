"""
A control-flow graph consists of a Start CfgNode, an End CfgNode, and some Node CfgNodes in between.
It looks like ``dict[CfgNode, list[CfgNode]]``, along with a start and end. You can reuse the dict to
contain multiple graphs. Each CfgNode has an index, an integer that uniquely identifies a CST node.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import libcst as cst
from libcst import matchers as m

from styx_compiler.metadata_providers import IndexProvider


@dataclass(frozen=True)
class Node:
    """
    A node in the control flow graph

    Uses an index from the IndexProvider to tie it to the CST, and an extra number to make multiple unique instances
    """

    index: int
    number: int


@dataclass(frozen=True)
class Start:
    """
    A start node in the control flow graph

    Uses an index from the IndexProvider to tie it to the CST
    """

    index: int


@dataclass(frozen=True)
class End:
    """
    An end node in the control flow graph

    Uses an index from the IndexProvider to tie it to the CST
    """

    index: int


@dataclass(frozen=True)
class Ghost:
    """
    Not a real node, just a construction device that gets removed later

    Uses an index from the IndexProvider to tie it to the CST, and an extra number to make multiple unique instances
    """

    index: int
    number: int


CfgNode = Node | Start | End | Ghost


class ComputeControlFlowGraph(cst.CSTVisitor):
    """
    Computes the control-flow graph of the code, expressed in indices from the IndexProvider
    """

    METADATA_DEPENDENCIES = (IndexProvider,)

    def __init__(self):
        super().__init__()
        self._cfg: dict[CfgNode, set[CfgNode]] = {}
        self._start_end: list[tuple[CfgNode, CfgNode]] = []

    def _edge(self, prev: list[CfgNode], cur: CfgNode) -> list[CfgNode]:
        for p in prev:
            if p not in self._cfg:
                self._cfg[p] = set()
            self._cfg[p].add(cur)
        return [cur]

    def _edges(self, prev: list[CfgNode], tos: list[CfgNode]) -> list[CfgNode]:
        for p in prev:
            if p not in self._cfg:
                self._cfg[p] = set()
            for to in tos:
                self._cfg[p].add(to)
        return tos

    def _make_cfg_node(self, cst_node: cst.CSTNode, number: int, prev: list[CfgNode]) -> list[CfgNode]:
        cur = Node(self.get_metadata(IndexProvider, cst_node), number)
        return self._edge(prev, cur)

    def _clean_up_cfg_ghosts(self, start: Start) -> None:
        seen: set[CfgNode] = set()
        workstack: list[CfgNode] = [start]
        seen.add(start)
        while len(workstack) > 0:
            node = workstack.pop()
            ghost_workstack: list[Ghost] = []
            for next_node in self._cfg.get(node, set()):
                if isinstance(next_node, Ghost):
                    ghost_workstack.append(next_node)
                elif next_node not in seen:
                    workstack.append(next_node)
                    seen.add(next_node)
            while len(ghost_workstack) > 0:
                next_node = ghost_workstack.pop()
                self._cfg[node].remove(next_node)
                for next_next_node in self._cfg[next_node]:
                    self._cfg[node].add(next_next_node)
                    if isinstance(next_next_node, Ghost):
                        ghost_workstack.append(next_next_node)
                    elif next_node not in seen:
                        workstack.append(next_node)
                        seen.add(next_node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        index = self.get_metadata(IndexProvider, node)
        start = Start(index)
        end = End(index)
        self._start_end.append((start, end))

        prev = [start]

        number = 0

        for param in node.params.params:
            cur = Node(self.get_metadata(IndexProvider, param), number)
            self._edge(prev, cur)
            prev = [cur]

        prev = self._visit_BaseSuite(node.body, number, prev, fn_end=end, exception_targets=[end])

        self._edge(prev, end)

        self._clean_up_cfg_ghosts(start)

    def _visit_BaseSuite(
        self,
        statements: cst.BaseSuite | cst.SimpleStatementLine,
        number: int,
        prev: list[CfgNode],
        fn_end: End,
        exception_targets: list[CfgNode],
        loop_continue_target: CfgNode | None = None,
        loop_break_target: CfgNode | None = None,
    ) -> list[CfgNode]:
        for statement in statements.body:
            prev = self._visit_statement(
                statement,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        return prev

    def _visit_statement(
        self,
        statement: cst.BaseStatement | cst.BaseSmallStatement,
        number: int,
        prev: list[CfgNode],
        fn_end: End,
        exception_targets: list[CfgNode],
        loop_continue_target: CfgNode | None = None,
        loop_break_target: CfgNode | None = None,
    ) -> list[CfgNode]:
        ## Simple Statements
        if m.matches(statement, m.AnnAssign()):
            statement: cst.AnnAssign = cst.ensure_type(statement, cst.AnnAssign)
            # RHS first if it exists
            if statement.value is not None:
                prev = self._visit_expression(statement.value, number, prev)
            # LHS
            prev = self._visit_expression(statement.target, number, prev)
        elif m.matches(statement, m.Assert()):
            statement: cst.Assert = cst.ensure_type(statement, cst.Assert)
            # test expression first?
            prev = self._visit_expression(statement.test, number, prev)
            # then message
            prev = self._visit_expression(statement.msg, number, prev)
        elif m.matches(statement, m.Assign()):
            statement: cst.Assign = cst.ensure_type(statement, cst.Assign)
            # RHS first
            prev = self._visit_expression(statement.value, number, prev)
            # then the multiple LHS, from left to right
            for target in statement.targets:
                prev = self._make_cfg_node(target, number, prev)  # AssignTarget
        elif m.matches(statement, m.AugAssign()):
            statement: cst.AugAssign = cst.ensure_type(statement, cst.AugAssign)
            # note we're making the AugAssign a node first to represent reading the value from the target
            prev = self._make_cfg_node(statement, number, prev)  # AugAssign
            # then we visit the RHS expression to find more reads
            prev = self._visit_expression(statement.value, number, prev)
            # finally we write to the LHS
            prev = self._visit_expression(statement.target, number, prev)
        elif m.matches(statement, m.Break()):
            if loop_break_target is None:
                msg = "Found break outside of loop"
                raise RuntimeError(msg)
            self._edge(prev, loop_break_target)
            prev = []
        elif m.matches(statement, m.Continue()):
            if loop_continue_target is None:
                msg = "Found break outside of loop"
                raise RuntimeError(msg)
            self._edge(prev, loop_continue_target)
            prev = []
        elif m.matches(statement, m.Del()):
            statement: cst.Del = cst.ensure_type(statement, cst.Del)
            prev = self._visit_expression(statement.target, number, prev)
        elif m.matches(statement, m.Expr()):
            statement: cst.Expr = cst.ensure_type(statement, cst.Expr)
            prev = self._visit_expression(statement.value, number, prev)
        elif m.matches(statement, m.Global()):
            statement: cst.Global = cst.ensure_type(statement, cst.Global)
            for name in statement.names:
                prev = self._make_cfg_node(name, number, prev)  # NameItem
        elif m.matches(statement, m.Import()):
            statement: cst.Import = cst.ensure_type(statement, cst.Import)
            for name in statement.names:
                prev = self._visit_ImportAlias(name, number, prev)
        elif m.matches(statement, m.ImportFrom()):
            statement: cst.ImportFrom = cst.ensure_type(statement, cst.ImportFrom)
            if statement.module is not None:
                prev = self._make_cfg_node(statement.module, number, prev)  # Attribute | Name
            if not m.matches(statement.names, m.ImportStar()):
                for name in statement.names:
                    prev = self._visit_ImportAlias(name, number, prev)
        elif m.matches(statement, m.Nonlocal()):
            statement: cst.Nonlocal = cst.ensure_type(statement, cst.Nonlocal)
            for name in statement.names:
                prev = self._make_cfg_node(name, number, prev)  # NameItem
        elif m.matches(statement, m.Pass()):
            pass
        elif m.matches(statement, m.Raise()):
            statement: cst.Raise = cst.ensure_type(statement, cst.Raise)
            if statement.exc is not None:
                prev = self._visit_expression(statement.exc, number, prev)
            if statement.cause is not None:
                prev = self._visit_expression(statement.cause.item, number, prev)
            self._edges(prev, exception_targets)
            prev = []
        elif m.matches(statement, m.Return()):
            statement: cst.Return = cst.ensure_type(statement, cst.Return)
            prev = self._visit_expression(statement.value, number, prev)
            self._edge(prev, fn_end)
            prev = []
        ## Compound Statements
        elif m.matches(statement, m.ClassDef()):
            msg = "Inline class definition is not yet supported"
            raise NotImplementedError(msg)
        elif m.matches(statement, m.For()):
            statement: cst.For = cst.ensure_type(statement, cst.For)
            index = self.get_metadata(IndexProvider, statement)
            for_loop_continue_target = Ghost(index, 0)
            prev = self._edge(prev, for_loop_continue_target)
            loop_expr_prev = self._visit_expression(statement.iter, number, prev)
            prev = loop_expr_prev
            prev = self._visit_expression(statement.target, number, prev)
            prev = self._visit_loop(
                statement,
                number,
                prev,
                index,
                for_loop_continue_target,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        elif m.matches(statement, m.FunctionDef()):
            msg = "Inline function definition is not yet supported"
            raise NotImplementedError(msg)
        elif m.matches(statement, m.If()):
            statement: cst.If = cst.ensure_type(statement, cst.If)
            prev = self._visit_expression(statement.test, number, prev)
            body = self._visit_BaseSuite(
                statement.body,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
            if statement.orelse is None:
                pass
            elif m.matches(statement.orelse, m.Else()):
                orelse: cst.Else = cst.ensure_type(statement.orelse, cst.Else)
                prev = self._visit_BaseSuite(
                    orelse.body,
                    number,
                    prev,
                    fn_end=fn_end,
                    exception_targets=exception_targets,
                    loop_continue_target=loop_continue_target,
                    loop_break_target=loop_break_target,
                )
            else:
                orelse: cst.If = cst.ensure_type(statement.orelse, cst.If)
                prev = self._visit_statement(
                    orelse,
                    number,
                    prev,
                    fn_end=fn_end,
                    exception_targets=exception_targets,
                    loop_continue_target=loop_continue_target,
                    loop_break_target=loop_break_target,
                )
            prev = [*body, *prev]
        elif m.matches(statement, m.Try()):
            # TODO: suspicious, probably wrong around finally statements in nested try
            statement: cst.Try = cst.ensure_type(statement, cst.Try)
            handler_entries = []
            handler_cond = []
            handler_exits = []
            for handler in statement.handlers:
                handler: cst.ExceptHandler = cst.ensure_type(handler, cst.ExceptHandler)  # noqa: PLW2901
                handler_index = self.get_metadata(IndexProvider, handler)
                handler_entry = Ghost(handler_index, 0)
                for_loop_break_target = Ghost(handler_index, 1)
                handler_exit = for_loop_break_target
                handler_entries.append(handler_entry)
                if len(handler_cond) > 0:
                    self._edge(handler_cond[-1], handler_entry)
                handler_prev = self._visit_expression(handler.type, number, [handler_entry])
                handler_cond.append(handler_prev)
                self._edge(handler_prev, handler_exit)
                handler_exits.append(handler_exit)

                if handler.name is not None:
                    handler_prev = self._make_cfg_node(handler.name, number, handler_prev)  # AsName
                handler_prev = self._visit_BaseSuite(
                    handler.body,
                    number,
                    handler_prev,
                    fn_end=fn_end,
                    exception_targets=exception_targets,
                    loop_continue_target=loop_continue_target,
                    loop_break_target=loop_break_target,
                )
                self._edge(handler_prev, handler_exit)
            prev = self._visit_BaseSuite(
                statement.body,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=handler_entries,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
            if len(handler_entries) > 0:
                self._edge(prev, handler_entries[0])
                prev = handler_cond[-1]
            if statement.orelse is not None:
                orelse: cst.Else = cst.ensure_type(statement.orelse, cst.Else)
                prev = self._visit_BaseSuite(
                    orelse.body,
                    number,
                    prev,
                    fn_end=fn_end,
                    exception_targets=exception_targets,
                    loop_continue_target=loop_continue_target,
                    loop_break_target=loop_break_target,
                )
            prev = [*prev, *handler_exits]
            if statement.finalbody is not None:
                finalbody: cst.Finally = cst.ensure_type(statement.finalbody, cst.Finally)
                prev = self._visit_BaseSuite(
                    finalbody.body,
                    number,
                    prev,
                    fn_end=fn_end,
                    exception_targets=exception_targets,
                    loop_continue_target=loop_continue_target,
                    loop_break_target=loop_break_target,
                )
                # Alternative way to exit finally if the try/except did a return or the except raised again.
                # This is the simple, coarse-grained version, probably better to instantiate the finalbody multiple
                #  times, that'll also be more easily correct (related to above TODO)
                self._edge(prev, fn_end)
        elif m.matches(statement, m.While()):
            statement: cst.While = cst.ensure_type(statement, cst.While)
            index = self.get_metadata(IndexProvider, statement)
            while_loop_continue_target = Ghost(index, 0)
            prev = self._edge(prev, while_loop_continue_target)
            prev = self._visit_expression(statement.test, number, prev)
            prev = self._visit_loop(
                statement,
                number,
                prev,
                index,
                while_loop_continue_target,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        elif m.matches(statement, m.With()):
            statement: cst.With = cst.ensure_type(statement, cst.With)
            for item in statement.items:
                prev = self._visit_expression(item.item, number, prev)
                if item.asname is not None:
                    prev = self._make_cfg_node(item.asname, number, prev)  # AsName
            prev = self._visit_BaseSuite(
                statement.body,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        ## Statement Blocks
        elif m.matches(statement, m.SimpleStatementLine() | m.SimpleStatementSuite() | m.IndentedBlock()):
            if m.matches(statement, m.SimpleStatementLine()):
                statement: cst.SimpleStatementLine = cst.ensure_type(statement, cst.SimpleStatementLine)
            else:
                statement: cst.BaseSuite = cst.ensure_type(statement, cst.BaseSuite)
            prev = self._visit_BaseSuite(
                statement,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        else:
            msg = f"Unknown statement type {statement}"
            raise RuntimeError(msg)

        return prev

    def _visit_loop(
        self,
        statement: cst.For | cst.While,
        number: int,
        prev: list[CfgNode],
        index: int,
        this_loop_continue_target: Ghost,
        fn_end: End,
        exception_targets: list[CfgNode],
        loop_continue_target: CfgNode | None,
        loop_break_target: CfgNode | None,
    ) -> list[CfgNode]:
        this_loop_break_target = Ghost(index, 1)
        prev = self._visit_BaseSuite(
            statement.body,
            number,
            prev,
            fn_end=fn_end,
            exception_targets=exception_targets,
            loop_continue_target=this_loop_continue_target,
            loop_break_target=this_loop_break_target,
        )
        if statement.orelse is not None:
            orelse: cst.Else = cst.ensure_type(statement.orelse, cst.Else)
            prev = self._visit_BaseSuite(
                orelse.body,
                number,
                prev,
                fn_end=fn_end,
                exception_targets=exception_targets,
                loop_continue_target=loop_continue_target,
                loop_break_target=loop_break_target,
            )
        return self._edge(prev, this_loop_break_target)

    def _visit_expression(self, expression: cst.BaseExpression, number: int, prev: list[CfgNode]) -> list[CfgNode]:
        ## Names and Object Attributes
        if m.matches(expression, m.Name()):
            prev = self._make_cfg_node(expression, number, prev)  # Name
        elif m.matches(expression, m.Attribute()):
            expression: cst.Attribute = cst.ensure_type(expression, cst.Attribute)
            prev = self._visit_expression(expression.value, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # Attribute
        ## Operations and Comparisons
        elif m.matches(expression, m.UnaryOperation()):
            expression: cst.UnaryOperation = cst.ensure_type(expression, cst.UnaryOperation)
            prev = self._visit_expression(expression.expression, number, prev)
            prev = self._make_cfg_node(expression.expression, number, prev)  # UnaryOperation
        elif m.matches(expression, m.BinaryOperation() | m.BooleanOperation()):
            expression: cst.BinaryOperation = cst.ensure_type(expression, cst.BinaryOperation)
            prev = self._visit_expression(expression.left, number, prev)
            prev = self._visit_expression(expression.right, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # BinaryOperation, BooleanOperation
        elif m.matches(expression, m.Comparison()):
            # noinspection DuplicatedCode
            expression: cst.Comparison = cst.ensure_type(expression, cst.Comparison)
            prev = self._visit_expression(expression.left, number, prev)
            for comparison in expression.comparisons:
                prev = self._visit_expression(comparison.comparator, number, prev)
                prev = self._make_cfg_node(comparison, number, prev)
        ## Control Flow
        elif m.matches(expression, m.Await()):
            expression: cst.Await = cst.ensure_type(expression, cst.Await)
            prev = self._visit_expression(expression.expression, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # Await
        elif m.matches(expression, m.Yield()):
            expression: cst.Yield = cst.ensure_type(expression, cst.Yield)
            prev = self._visit_expression(expression.value, number, prev)
            # yield is not like return. A later call to the generator will continue from the yield, so
            #  in the CFG we're just going to represent it as a normal node and pretend the control did
            #  not leave and re-enter because it probably doesn't matter for the analyses we want to do.
            prev = self._make_cfg_node(expression, number, prev)  # Yield
        elif m.matches(expression, m.From()):
            expression: cst.From = cst.ensure_type(expression, cst.From)
            prev = self._visit_expression(expression.item, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # From
        elif m.matches(expression, m.IfExp()):
            expression: cst.IfExp = cst.ensure_type(expression, cst.IfExp)
            prev = self._visit_expression(expression.test, number, prev)
            body = self._visit_expression(expression.body, number, prev)
            orelse = self._visit_expression(expression.orelse, number, prev)
            prev = [*body, *orelse]
        ## Lambdas and Function Calls
        elif m.matches(expression, m.Lambda()):
            msg = "Lambdas are not yet supported"
            raise NotImplementedError(msg)
        elif m.matches(expression, m.Call()):
            expression: cst.Call = cst.ensure_type(expression, cst.Call)
            prev = self._visit_expression(expression.func, number, prev)
            for arg in expression.args:
                prev = self._visit_expression(arg.value, number, prev)
        ## Literal Values
        elif m.matches(expression, m.Ellipsis()):
            pass
        elif m.matches(expression, m.Integer() | m.Float() | m.Imaginary() | m.SimpleString() | m.ConcatenatedString()):
            prev = self._make_cfg_node(
                expression, number, prev
            )  # Integer, Float, Imaginary, SimpleString, ConcatenatedString
        elif m.matches(expression, m.FormattedString()):
            expression: cst.FormattedString = cst.ensure_type(expression, cst.FormattedString)
            for part in expression.parts:
                if m.matches(part, m.FormattedStringExpression()):
                    part: cst.FormattedStringExpression = cst.ensure_type(part, cst.FormattedStringExpression)  # noqa: PLW2901
                    prev = self._visit_expression(part.expression, number, prev)
                    prev = self._make_cfg_node(part, number, prev)  # FormattedStringExpression
            prev = self._make_cfg_node(expression, number, prev)  # FormattedString
        ## Collections
        elif m.matches(expression, m.Tuple() | m.List() | m.Set()):
            # noinspection PyUnresolvedReferences
            prev = self._visit_elements(expression.elements, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # Tuple, List, Set
        elif m.matches(expression, m.Element() | m.StarredElement()):
            # noinspection PyUnresolvedReferences
            prev = self._visit_expression(expression.value, number, prev)
            prev = self._make_cfg_node(expression, number, prev)  # Element, StarredElement
        elif m.matches(expression, m.Dict()):
            expression: cst.Dict = cst.ensure_type(expression, cst.Dict)
            for element in expression.elements:
                if m.matches(element, m.DictElement()):
                    element: cst.DictElement = cst.ensure_type(element, cst.DictElement)  # noqa: PLW2901
                    prev = self._visit_expression(element.key, number, prev)
                prev = self._visit_expression(element.value, number, prev)
                prev = self._make_cfg_node(element, number, prev)  # DictElement, StarredDictElement
        ## Comprehensions
        elif m.matches(expression, m.GeneratorExp() | m.ListComp() | m.SetComp()):
            expression: cst.BaseSimpleComp = cst.ensure_type(expression, cst.BaseSimpleComp)
            prev = self._visit_CompFor(expression.for_in, number, expression.elt, prev)
            prev = self._make_cfg_node(expression, number, prev)  # GeneratorExp, ListComp, SetComp
        elif m.matches(expression, m.DictComp()):
            expression: cst.DictComp = cst.ensure_type(expression, cst.DictComp)
            prev = self._visit_CompFor(expression.for_in, number, (expression.key, expression.value), prev)
            prev = self._make_cfg_node(expression, number, prev)  # DictComp
        ## Subscripts and Slices
        elif m.matches(expression, m.Subscript()):
            expression: cst.Subscript = cst.ensure_type(expression, cst.Subscript)
            prev = self._visit_expression(expression.value, number, prev)
            for element in expression.slice:
                if m.matches(element, m.Index()):
                    element: cst.Index = cst.ensure_type(element, cst.Index)  # noqa: PLW2901
                    prev = self._visit_expression(element.value, number, prev)
                    prev = self._make_cfg_node(element, number, prev)  # Index
                elif m.matches(element, m.Slice()):
                    element: cst.Slice = cst.ensure_type(element, cst.Slice)  # noqa: PLW2901
                    prev = self._visit_expression(element.lower, number, prev)
                    prev = self._visit_expression(element.upper, number, prev)
                    prev = self._visit_expression(element.step, number, prev)
                    prev = self._make_cfg_node(element, number, prev)  # Slice
                else:
                    msg = f"Unknown subscript element type {element}"
                    raise RuntimeError(msg)
            prev = self._make_cfg_node(expression, number, prev)  # Subscript
        else:
            msg = f"Unknown expression type {expression}"
            raise RuntimeError(msg)
        return prev

    def _visit_ImportAlias(self, import_alias: cst.ImportAlias, number: int, prev: list[CfgNode]) -> list[CfgNode]:
        prev = self._make_cfg_node(import_alias.name, number, prev)  # Attribute | Name
        if import_alias.asname is not None:
            prev = self._make_cfg_node(import_alias.asname, number, prev)  # AsName
        return prev

    def _visit_elements(self, elements: Sequence[cst.BaseElement], number: int, prev: list[CfgNode]) -> list[CfgNode]:
        for element in elements:
            if m.matches(element, m.Element() | m.StarredElement()):
                prev = self._visit_expression(element.value, number, prev)
                prev = self._make_cfg_node(element, number, prev)  # Element, StarredElement
            else:
                msg = f"Unknown element type {element}"
                raise RuntimeError(msg)
        return prev

    def _visit_CompFor(
        self,
        for_in: cst.CompFor,
        number: int,
        elt: cst.BaseExpression | tuple[cst.BaseExpression, cst.BaseExpression],
        prev: list[CfgNode],
    ) -> list[CfgNode]:
        exit = Ghost(self.get_metadata(IndexProvider, for_in), 0)
        prev = self._visit_expression(for_in.iter, number, prev)
        prev = self._visit_expression(for_in.target, number, prev)
        for compif in for_in.ifs:
            prev = self._visit_expression(compif.test, number, prev)
            self._edge(prev, exit)
        if for_in.inner_for_in is not None:
            prev = self._visit_CompFor(for_in.inner_for_in, number, elt, prev)
        else:
            if isinstance(elt, tuple):
                key, value = elt
                prev = self._visit_expression(key, number, prev)
                prev = self._visit_expression(value, number, prev)
            else:
                prev = self._visit_expression(elt, number, prev)
        return self._edge(prev, exit)

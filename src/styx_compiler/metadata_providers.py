import libcst as cst
import libcst.matchers as m


class IndexProvider(cst.VisitorMetadataProvider[int]):
    """
    Gives each CST node a number to refer to them uniquely
    """

    def __init__(self, index: int = 0):
        super().__init__()
        self._index = index

    def on_visit(self, node: cst.CSTNode) -> bool:
        if m.matches(node, m.SimpleWhitespace() | m.TrailingWhitespace()):
            return False
        if not self.get_metadata(type(self), node, False):
            self.set_metadata(node, self._index)
            self._index += 1
            return True
        return False


class ComputeLiveVariables(cst.CSTVisitor):
    """
    Computes the live variables, using the indices from IndexProvider to navigate the control-flow graph
    """

    METADATA_DEPENDENCIES = (IndexProvider,)


class LiveVariablesProvider(cst.VisitorMetadataProvider[list[cst.Name]]):
    METADATA_DEPENDENCIES = (IndexProvider,)

    def __init__(self, live_variables_entry: list[list[cst.Name]], live_variables_exit: list[list[cst.Name]]):
        super().__init__()
        self.live_variables_entry = live_variables_entry
        self.live_variables_exit = live_variables_exit

    # visit the same things as the IndexProvider and look up the list of live variables from the index.

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

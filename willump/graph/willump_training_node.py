from willump.graph.willump_graph_node import WillumpGraphNode
from willump.graph.willump_python_node import WillumpPythonNode
from willump.graph.willump_model_node import WillumpModelNode

from typing import List
import ast


class WillumpTrainingNode(WillumpPythonNode, WillumpModelNode):
    """
    Willump Training Node.  Contains Python code to train a model.
    """
    def __init__(self, python_ast: ast.AST, input_names: List[str], output_names: List[str],
                 in_nodes: List[WillumpGraphNode], is_async_node: bool = False) -> None:
        super(WillumpTrainingNode, self).__init__(python_ast, input_names, output_names, in_nodes, is_async_node)

    def __repr__(self):
        return "Willump Training Python node with inputs %s\n outputs %s\n and code %s\n" % \
               (str(list(map(lambda x: x.get_output_names(), self._in_nodes))), self.output_names,
                ast.dump(self._python_ast))

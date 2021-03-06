from willump.graph.willump_graph_node import WillumpGraphNode

from weld.types import *
from typing import List


class ArrayBinopNode(WillumpGraphNode):
    """
    Willump Array Binop Node.  Add, subtract, multiply, or divide two arrays.
    """
    _input_nodes: List[WillumpGraphNode]
    _input_one_name: str
    _input_two_name: str
    _output_name: str
    _output_type: WeldType
    _binop_str: str

    def __init__(self,
                 input_one_node: WillumpGraphNode,
                 input_two_node: WillumpGraphNode,
                 input_one_name: str,
                 input_two_name: str,
                 output_name: str,
                 output_type: WeldType,
                 binop_str: str) -> None:
        self._input_nodes = [input_one_node, input_two_node]
        self._input_one_name = input_one_name
        self._input_two_name = input_two_name
        self._input_names = [input_one_name, input_two_name]
        self._output_name = output_name
        self._output_type = output_type
        self._binop_str = binop_str

    def get_in_nodes(self) -> List[WillumpGraphNode]:
        return self._input_nodes

    def get_in_names(self) -> List[str]:
        return self._input_names

    def get_node_weld(self) -> str:
        weld_program = \
            """
            let OUTPUT_NAME = result(for(zip(INPUT_ONE_NAME, INPUT_TWO_NAME),
                appender[OUTPUT_ELEMTYPE],
                | bs: appender[OUTPUT_ELEMTYPE], i: i64, one_two |
                    merge(bs, OUTPUT_ELEMTYPE(one_two.$0) BINOP_STR OUTPUT_ELEMTYPE(one_two.$1))
            ));
            """
        weld_program = weld_program.replace("OUTPUT_ELEMTYPE", str(self._output_type.elemType))
        weld_program = weld_program.replace("INPUT_ONE_NAME", self._input_one_name)
        weld_program = weld_program.replace("INPUT_TWO_NAME", self._input_two_name)
        weld_program = weld_program.replace("OUTPUT_NAME", self._output_name)
        weld_program = weld_program.replace("BINOP_STR", self._binop_str)
        return weld_program

    def get_output_name(self) -> str:
        return self._output_name

    def get_output_names(self) -> List[str]:
        return [self._output_name]

    def __repr__(self):
        return "Array Addition node for inputs {0} {1} output {2}\n"\
            .format(self._input_one_name, self._input_two_name, self._output_name)

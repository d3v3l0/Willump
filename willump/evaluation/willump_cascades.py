import ast
import copy
from typing import MutableMapping, List, Tuple, Mapping, Optional
from sklearn.model_selection import train_test_split
import numpy as np
import pandas as pd
import scipy.sparse

import willump.evaluation.willump_graph_passes as wg_passes
from willump import *
from willump.graph.array_count_vectorizer_node import ArrayCountVectorizerNode
from willump.graph.array_tfidf_node import ArrayTfIdfNode
from willump.graph.cascade_column_selection_node import CascadeColumnSelectionNode
from willump.graph.cascade_combine_predictions_node import CascadeCombinePredictionsNode
from willump.graph.cascade_point_early_exit_node import CascadePointEarlyExitNode
from willump.graph.cascade_stack_dense_node import CascadeStackDenseNode
from willump.graph.cascade_stack_sparse_node import CascadeStackSparseNode
from willump.graph.cascade_threshold_proba_node import CascadeThresholdProbaNode
from willump.graph.cascade_topk_selection_node import CascadeTopKSelectionNode
from willump.graph.hash_join_node import WillumpHashJoinNode
from willump.graph.identity_node import IdentityNode
from willump.graph.pandas_column_selection_node import PandasColumnSelectionNode
from willump.graph.pandas_column_selection_node_python import PandasColumnSelectionNodePython
from willump.graph.pandas_dataframe_concatenation_node import PandasDataframeConcatenationNode
from willump.graph.pandas_series_concatenation_node import PandasSeriesConcatenationNode
from willump.graph.pandas_to_dense_matrix_node import PandasToDenseMatrixNode
from willump.graph.reshape_node import ReshapeNode
from willump.graph.stack_dense_node import StackDenseNode
from willump.graph.stack_sparse_node import StackSparseNode
from willump.graph.willump_graph_node import WillumpGraphNode
from willump.graph.willump_input_node import WillumpInputNode
from willump.graph.willump_model_node import WillumpModelNode
from willump.graph.willump_predict_node import WillumpPredictNode
from willump.graph.willump_predict_proba_node import WillumpPredictProbaNode
from willump.graph.willump_python_node import WillumpPythonNode
from willump.graph.willump_training_node import WillumpTrainingNode
from willump.willump_utilities import *


def graph_from_input_sources(node: WillumpGraphNode, selected_input_sources: List[WillumpGraphNode],
                             typing_map: MutableMapping[str, WeldType],
                             base_discovery_dict, which: str, small_model_output_node=None) \
        -> Optional[WillumpGraphNode]:
    """
    Take in a node and a list of input sources.  Return a node that only depends on the intersection of its
    original input sources and the list of input sources.  Return None if a node depends on no node in the list.

    This function should not modify the original graph or any of its nodes.

    It does modify the type map.
    """

    def graph_from_input_sources_recursive(node):
        return_node = None
        if isinstance(node, ArrayCountVectorizerNode) or isinstance(node, ArrayTfIdfNode):
            if node in selected_input_sources:
                if small_model_output_node is not None:
                    node.push_cascade(small_model_output_node)
                return_node = node
        elif isinstance(node, StackSparseNode):
            node_input_nodes = node.get_in_nodes()
            node_input_names = node.get_in_names()
            node_output_name = node.get_output_name()
            new_output_name = ("cascading__%s__" % which) + node_output_name
            node_output_type = node.get_output_type()
            new_input_nodes, new_input_names = [], []
            for node_input_node, node_input_name in zip(node_input_nodes, node_input_names):
                return_node = graph_from_input_sources_recursive(node_input_node)
                if return_node is not None:
                    new_input_nodes.append(return_node)
                    new_input_names.append(return_node.get_output_names()[0])
            return_node = StackSparseNode(input_nodes=new_input_nodes,
                                          input_names=new_input_names,
                                          output_name=new_output_name,
                                          output_type=node_output_type)
            typing_map[new_output_name] = node_output_type
        elif isinstance(node, StackDenseNode):
            node_input_nodes = node.get_in_nodes()
            node_input_names = node.get_in_names()
            node_output_name = node.get_output_name()
            new_output_name = ("cascading__%s__" % which) + node_output_name
            node_output_type = node.get_output_type()
            new_input_nodes, new_input_names = [], []
            for node_input_node, node_input_name in zip(node_input_nodes, node_input_names):
                return_node = graph_from_input_sources_recursive(node_input_node)
                if return_node is not None:
                    new_input_nodes.append(return_node)
                    new_input_names.append(return_node.get_output_names()[0])
            return_node = StackDenseNode(input_nodes=new_input_nodes,
                                         input_names=new_input_names,
                                         output_name=new_output_name,
                                         output_type=node_output_type)
            typing_map[new_output_name] = node_output_type
        elif isinstance(node, PandasColumnSelectionNode) or isinstance(node, PandasColumnSelectionNodePython):
            node_input_nodes = node.get_in_nodes()
            new_input_nodes = [graph_from_input_sources_recursive(node_input_node) for
                               node_input_node in node_input_nodes]
            if not all(new_input_node is None for new_input_node in new_input_nodes):
                assert all(len(new_input_node.get_output_names()) == 1 for new_input_node in new_input_nodes)
                new_input_names = [new_input_node.get_output_name() for new_input_node in new_input_nodes]
                node_output_name = node.get_output_name()
                new_output_name = ("cascading__%s__" % which) + node_output_name
                new_input_types: List[WeldType] = [new_input_node.get_output_type() for new_input_node in
                                                   new_input_nodes]
                selected_columns = node.selected_columns
                new_selected_columns = list(
                    filter(lambda x: any(x in new_input_type.column_names for new_input_type in new_input_types),
                           selected_columns))
                orig_output_type = node.get_output_type()
                if isinstance(orig_output_type, WeldPandas):
                    # Hacky code so that the pre-joins dataframe columns don't appear twice in the full output.
                    try:
                        base_node = wg_passes.find_dataframe_base_node(node_input_nodes[0], base_discovery_dict)
                        assert (isinstance(base_node, WillumpHashJoinNode))
                        if base_node.get_in_nodes()[0] not in selected_input_sources:
                            base_left_df_col_names = base_node.left_df_type.column_names
                            new_selected_columns = list(
                                filter(lambda x: x not in base_left_df_col_names, new_selected_columns))
                        col_map = {col_name: col_type for col_name, col_type in
                                   zip(orig_output_type.column_names, orig_output_type.field_types)}
                        new_field_types = [col_map[col_name] for col_name in new_selected_columns]
                    except AssertionError:
                        col_map = {col_name: col_type for col_name, col_type in
                                   zip(orig_output_type.column_names, orig_output_type.field_types)}
                        new_field_types = [col_map[col_name] for col_name in new_selected_columns]
                    new_output_type = WeldPandas(field_types=new_field_types, column_names=new_selected_columns)
                else:
                    assert (isinstance(orig_output_type, WeldSeriesPandas))
                    new_output_type = WeldSeriesPandas(elemType=orig_output_type.elemType,
                                                       column_names=new_selected_columns)
                if isinstance(node, PandasColumnSelectionNode):
                    return_node = PandasColumnSelectionNode(input_nodes=new_input_nodes,
                                                            input_names=new_input_names,
                                                            output_name=new_output_name,
                                                            input_types=new_input_types,
                                                            selected_columns=new_selected_columns,
                                                            output_type=new_output_type)
                else:
                    return_node = PandasColumnSelectionNodePython(input_nodes=new_input_nodes,
                                                                  input_names=new_input_names,
                                                                  output_name=new_output_name,
                                                                  input_types=new_input_types,
                                                                  selected_columns=new_selected_columns,
                                                                  output_type=new_output_type)
                typing_map[new_output_name] = return_node.get_output_type()
        elif isinstance(node, WillumpHashJoinNode):
            base_node = wg_passes.find_dataframe_base_node(node, base_discovery_dict)
            node_input_node = node.get_in_nodes()[0]
            if node in selected_input_sources:
                if node is base_node:
                    new_input_node = base_node.get_in_nodes()[0]
                    new_input_name = base_node.get_in_names()[0]
                    new_input_type = base_node.left_df_type
                else:
                    new_input_node = graph_from_input_sources_recursive(node_input_node)
                    if new_input_node is None:
                        new_input_node = base_node.get_in_nodes()[0]
                        new_input_name = base_node.get_in_names()[0]
                        new_input_type = base_node.left_df_type
                    else:
                        assert (isinstance(new_input_node, WillumpHashJoinNode))
                        new_input_name = new_input_node.get_output_name()
                        new_input_type = new_input_node.get_output_type()
                return_node = copy.copy(node)
                if small_model_output_node is not None:
                    return_node.push_cascade(small_model_output_node)
                return_node._input_nodes = copy.copy(node._input_nodes)
                return_node._input_nodes[0] = new_input_node
                return_node._input_names = copy.copy(node._input_names)
                return_node._input_names[0] = new_input_name
                return_node.left_input_name = new_input_name
                return_node.left_df_type = new_input_type
                return_node._output_type = \
                    WeldPandas(field_types=new_input_type.field_types + node.right_df_type.field_types,
                               column_names=new_input_type.column_names + node.right_df_type.column_names)
                typing_map[return_node.get_output_name()] = return_node.get_output_type()
            elif node is not base_node:
                return graph_from_input_sources_recursive(node_input_node)
            else:
                pass
        elif isinstance(node, PandasSeriesConcatenationNode):
            node_input_nodes = node.get_in_nodes()
            node_input_names = node.get_in_names()
            node_output_name = node.get_output_name()
            new_output_name = ("cascading__%s__" % which) + node_output_name
            node_output_type: WeldSeriesPandas = node.get_output_type()
            new_input_nodes, new_input_names, new_input_types, new_output_columns = [], [], [], []
            for node_input_node, node_input_name in zip(node_input_nodes, node_input_names):
                return_node = graph_from_input_sources_recursive(node_input_node)
                if return_node is not None:
                    new_input_nodes.append(return_node)
                    new_input_names.append(node_input_name)
                    return_node_output_type = return_node.get_output_types()[0]
                    assert (isinstance(return_node_output_type, WeldSeriesPandas))
                    new_input_types.append(return_node_output_type)
                    new_output_columns += return_node_output_type.column_names
            new_output_type = WeldSeriesPandas(elemType=node_output_type.elemType, column_names=new_output_columns)
            return_node = PandasSeriesConcatenationNode(input_nodes=new_input_nodes,
                                                        input_names=new_input_names,
                                                        input_types=new_input_types,
                                                        output_type=new_output_type,
                                                        output_name=new_output_name)
            typing_map[new_output_name] = return_node.get_output_type()
        elif isinstance(node, PandasDataframeConcatenationNode):
            node_input_nodes = node.get_in_nodes()
            node_input_names = node.get_in_names()
            node_output_name = node.get_output_name()
            new_output_name = ("cascading__%s__" % which) + node_output_name
            new_input_nodes, new_input_names, new_input_types, new_output_columns, new_field_types \
                = [], [], [], [], []
            for node_input_node, node_input_name in zip(node_input_nodes, node_input_names):
                return_node = graph_from_input_sources_recursive(node_input_node)
                if return_node is not None:
                    new_input_nodes.append(return_node)
                    new_input_names.append(node_input_name)
                    return_node_output_type = return_node.get_output_types()[0]
                    assert (isinstance(return_node_output_type, WeldPandas))
                    new_input_types.append(return_node_output_type)
                    new_output_columns += return_node_output_type.column_names
                    new_field_types += return_node_output_type.field_types
            new_output_type = WeldPandas(field_types=new_field_types, column_names=new_output_columns)
            return_node = PandasDataframeConcatenationNode(input_nodes=new_input_nodes,
                                                           input_names=new_input_names,
                                                           input_types=new_input_types,
                                                           output_type=new_output_type,
                                                           output_name=new_output_name,
                                                           keyword_args=node.keyword_args)
            typing_map[new_output_name] = return_node.get_output_type()
        elif isinstance(node, IdentityNode):
            input_node = node.get_in_nodes()[0]
            new_input_node = graph_from_input_sources_recursive(input_node)
            if new_input_node is not None:
                output_name = node.get_output_name()
                new_output_name = ("cascading__%s__" % which) + output_name
                output_type = new_input_node.get_output_type()
                new_input_name = new_input_node.get_output_name()
                return_node = IdentityNode(input_node=new_input_node, input_name=new_input_name,
                                           output_name=new_output_name, output_type=output_type)
                typing_map[new_output_name] = output_type
        elif isinstance(node, ReshapeNode):
            input_node = node.get_in_nodes()[0]
            new_input_node = graph_from_input_sources_recursive(input_node)
            if new_input_node is not None:
                output_name = node.get_output_name()
                new_output_name = ("cascading__%s__" % which) + output_name
                output_type = node.get_output_type()
                new_input_name = new_input_node.get_output_name()
                return_node = ReshapeNode(input_node=new_input_node, input_name=new_input_name,
                                          output_name=new_output_name, output_type=output_type,
                                          reshape_args=node.reshape_args)
                typing_map[new_output_name] = output_type
        elif isinstance(node, PandasToDenseMatrixNode):
            input_node = node.get_in_nodes()[0]
            new_input_node = graph_from_input_sources_recursive(input_node)
            if new_input_node is not None:
                output_name = node.get_output_name()
                new_output_name = ("cascading__%s__" % which) + output_name
                output_type = node.get_output_type()
                new_input_name = new_input_node.get_output_name()
                return_node = PandasToDenseMatrixNode(input_node=new_input_node, input_name=new_input_name,
                                                      input_type=new_input_node.get_output_type(),
                                                      output_name=new_output_name, output_type=output_type)
                typing_map[new_output_name] = output_type
        elif isinstance(node, WillumpPythonNode):
            if node in selected_input_sources:
                if small_model_output_node is not None:
                    pass
                return_node = node
        elif isinstance(node, WillumpInputNode):
            if node in selected_input_sources:
                return_node = node
        else:
            panic("Unrecognized node found when making cascade %s" % node.__repr__())
        return return_node

    return graph_from_input_sources_recursive(node)


def get_model_node_dependencies(training_input_node: WillumpGraphNode, base_discovery_dict,
                                small_model_output_node=None) \
        -> List[WillumpGraphNode]:
    """
    Take in a training node's input.  Return a Weld block that constructs the training node's
    input from its input sources.
    """
    current_node_stack: List[WillumpGraphNode] = [training_input_node]
    # Nodes through which the model has been pushed which are providing output.
    output_block: List[WillumpGraphNode] = []
    while len(current_node_stack) > 0:
        input_node = current_node_stack.pop()
        if isinstance(input_node, ArrayCountVectorizerNode) or isinstance(input_node, ArrayTfIdfNode):
            output_block.insert(0, input_node)
        elif isinstance(input_node, StackSparseNode) or isinstance(input_node, PandasColumnSelectionNode) \
                or isinstance(input_node, PandasSeriesConcatenationNode) or isinstance(input_node, IdentityNode) \
                or isinstance(input_node, ReshapeNode) or isinstance(input_node, PandasToDenseMatrixNode) \
                or isinstance(input_node, WillumpInputNode) \
                or isinstance(input_node, PandasDataframeConcatenationNode) \
                or isinstance(input_node, PandasColumnSelectionNodePython) \
                or isinstance(input_node, StackDenseNode):
            output_block.insert(0, input_node)
            current_node_stack += input_node.get_in_nodes()
        elif isinstance(input_node, WillumpHashJoinNode):
            base_node = wg_passes.find_dataframe_base_node(input_node, base_discovery_dict)
            output_block.insert(0, input_node)
            if input_node is not base_node:
                join_left_input_node = input_node.get_in_nodes()[0]
                current_node_stack.append(join_left_input_node)
        elif isinstance(input_node, WillumpPythonNode):
            output_block.insert(0, input_node)
            node_output_types = input_node.get_output_types()
            if small_model_output_node is not None and \
                    len(node_output_types) == 1 and isinstance(node_output_types[0], WeldPandas) \
                    and len(input_node.get_in_nodes()) == 1:
                node_input_name = strip_linenos_from_var(input_node.get_in_names()[0])
                small_model_output_name = strip_linenos_from_var(small_model_output_node.get_output_names()[0])
                shorten_python_code = "%s = cascade_df_shorten(%s, %s)" % (node_input_name,
                                                                           node_input_name,
                                                                           small_model_output_name)
                shorten_python_ast: ast.Module = \
                    ast.parse(shorten_python_code, "exec")
                shorten_python_node = WillumpPythonNode(python_ast=shorten_python_ast.body[0],
                                                        input_names=[input_node.get_in_names()[0],
                                                                     small_model_output_node.get_output_names()[0]],
                                                        output_names=[input_node.get_in_names()[0]],
                                                        output_types=input_node.get_in_nodes()[0].get_output_types(),
                                                        in_nodes=[input_node, small_model_output_node])
                output_block.insert(0, shorten_python_node)
                input_node._in_nodes = [shorten_python_node]
            if input_node.does_not_modify_data:
                current_node_stack += input_node.get_in_nodes()
        else:
            panic("Unrecognized node found when making cascade dependencies: %s" % input_node.__repr__())
    return output_block


def create_indices_to_costs_map(training_node: WillumpModelNode) -> Mapping[tuple, float]:
    """
    Create a map from the indices of the features generated by an operator (used as a unique identifier of the
    operator) to the operator's cost.
    """
    training_node_inputs: Mapping[
        WillumpGraphNode, Union[Tuple[int, int], Mapping[str, int]]] = training_node.get_model_inputs()
    indices_to_costs_map: MutableMapping = {}
    for node, indices in training_node_inputs.items():
        if isinstance(indices, tuple):
            pass
        else:
            indices = tuple(indices.values())
        indices_to_costs_map[indices] = node.get_cost()
    return indices_to_costs_map


def split_model_inputs(model_node: WillumpModelNode, feature_importances, indices_to_costs_map,
                       more_important_cost_frac=0.5) -> \
        Tuple[List[WillumpGraphNode], List[WillumpGraphNode]]:
    """
    Use a model's feature importances to divide its inputs into those more and those less important.  Return
    lists of each.
    """
    training_node_inputs: Mapping[
        WillumpGraphNode, Union[Tuple[int, int], Mapping[str, int]]] = model_node.get_model_inputs()
    nodes_to_efficiencies: MutableMapping[WillumpGraphNode, float] = {}
    nodes_to_importances: MutableMapping[WillumpGraphNode, float] = {}
    nodes_to_costs: MutableMapping[WillumpGraphNode, float] = {}
    total_cost = 0
    for node, indices in training_node_inputs.items():
        if isinstance(indices, tuple):
            node_importance = feature_importances[indices]
        else:
            indices = tuple(indices.values())
            node_importance = feature_importances[indices]
        nodes_to_importances[node] = node_importance
        node_cost: float = indices_to_costs_map[indices]
        nodes_to_costs[node] = node_cost
        nodes_to_efficiencies[node] = node_importance / node_cost
        total_cost += node_cost
    ranked_inputs = sorted(nodes_to_efficiencies.keys(), key=lambda x: nodes_to_efficiencies[x], reverse=True)
    current_cost = 0
    current_importance = 0
    more_important_inputs = []
    for node in ranked_inputs:
        if current_cost == 0:
            average_efficiency = 0
        else:
            average_efficiency = current_importance / current_cost
        node_efficiency = nodes_to_importances[node] / nodes_to_costs[node]
        if node_efficiency < average_efficiency / 5:
            break
        if current_cost + nodes_to_costs[node] <= more_important_cost_frac * total_cost:
            more_important_inputs.append(node)
            if nodes_to_costs[node] > 0:
                current_importance += nodes_to_importances[node]
                current_cost += nodes_to_costs[node]
    for node in ranked_inputs:
        if nodes_to_costs[node] == 0 and node not in more_important_inputs:
            more_important_inputs.append(node)
    less_important_inputs = [entry for entry in ranked_inputs if entry not in more_important_inputs]
    return more_important_inputs, less_important_inputs


def calculate_feature_importance(x, y, train_predict_score_functions: tuple, model_inputs) -> Mapping[tuple, float]:
    """
    Calculate the importance of all operators' feature sets using mean decrease accuracy.
    Return a map from the indices of the features generated by an operator (used as a unique identifier of the
    operator) to the operator's importance.
    """
    willump_train_function, willump_predict_function, willump_score_function = train_predict_score_functions
    train_x, valid_x, train_y, valid_y = train_test_split(x, y, test_size=0.25, random_state=42)
    model = willump_train_function(train_x, train_y)
    base_preds = willump_predict_function(model, valid_x)
    base_score = willump_score_function(valid_y, base_preds)
    return_map = {}
    for node, indices in model_inputs.items():
        valid_x_copy = valid_x.copy()
        if scipy.sparse.issparse(valid_x):
            valid_x_copy = valid_x_copy.toarray()
        if isinstance(valid_x_copy, pd.DataFrame):
            valid_x_copy = valid_x_copy.values
        if isinstance(indices, tuple):
            start, end = indices
            for i in range(start, end):
                np.random.shuffle(valid_x_copy[:, i])
        else:
            indices = tuple(indices.values())
            for i in indices:
                np.random.shuffle(valid_x_copy[:, i])
        if scipy.sparse.issparse(valid_x):
            valid_x_copy = scipy.sparse.csr_matrix(valid_x_copy)
        shuffled_preds = willump_predict_function(model, valid_x_copy)
        shuffled_score = willump_score_function(valid_y, shuffled_preds)
        return_map[indices] = base_score - shuffled_score
        del valid_x_copy
    return return_map


def training_model_cascade_pass(sorted_nodes: List[WillumpGraphNode],
                                typing_map: MutableMapping[str, WeldType],
                                training_cascades: dict,
                                train_predict_score_functions: tuple) -> List[WillumpGraphNode]:
    """
    Take in a program training a model.  Rank features in the model by importance.  Partition
    features into "more important" and "less important."  Train a small model on only more important features
    and a big model on all features.
    """

    def get_combiner_node(mi_head: WillumpGraphNode, li_head: WillumpGraphNode, orig_node: WillumpGraphNode) \
            -> WillumpGraphNode:
        """
        Generate a node that will fuse node_one and node_two to match the output of orig_node.  Requires all
        three nodes be of the same type.
        """
        orig_output_type = orig_node.get_output_types()[0]
        if isinstance(orig_output_type, WeldCSR):
            return StackSparseNode(input_nodes=[mi_head, li_head],
                                   input_names=[mi_head.get_output_names()[0], li_head.get_output_names()[0]],
                                   output_name=orig_node.get_output_names()[0],
                                   output_type=orig_output_type)
        elif isinstance(orig_output_type, WeldVec):
            return StackDenseNode(input_nodes=[mi_head, li_head],
                                  input_names=[mi_head.get_output_names()[0], li_head.get_output_names()[0]],
                                  output_name=orig_node.get_output_names()[0],
                                  output_type=orig_output_type)
        elif isinstance(orig_output_type, WeldPandas):
            mi_output_type = mi_head.get_output_types()[0]
            li_output_type = li_head.get_output_types()[0]
            assert (isinstance(mi_output_type, WeldPandas) and isinstance(li_output_type, WeldPandas))
            new_selected_columns = mi_output_type.column_names + li_output_type.column_names
            new_field_types = mi_output_type.field_types + li_output_type.field_types
            assert (set(new_selected_columns) == set(orig_output_type.column_names))
            output_name = orig_node.get_output_names()[0]
            output_type = WeldPandas(field_types=new_field_types, column_names=new_selected_columns)
            typing_map[output_name] = output_type
            return PandasColumnSelectionNode(input_nodes=[mi_head, li_head],
                                             input_names=[mi_head.get_output_names()[0], li_head.get_output_names()[0]],
                                             output_name=output_name,
                                             input_types=[mi_output_type, li_output_type],
                                             selected_columns=new_selected_columns,
                                             output_type=output_type)
        else:
            panic("Unrecognized combiner output type %s" % orig_output_type)

    def recreate_training_node(new_x_node: WillumpGraphNode, orig_node: WillumpTrainingNode,
                               output_prefix) -> WillumpTrainingNode:
        """
        Create a node based on orig_node that uses new_input_node as its input and prefixes its output's name
        with output_prefix.
        """
        assert (isinstance(orig_node, WillumpTrainingNode))
        orig_x_name, orig_y_name = orig_node.x_name, orig_node.y_name
        orig_y_node = orig_node.y_node
        x_name = new_x_node.get_output_names()[0]
        orig_output_name = orig_node.get_output_name()
        new_output_name = output_prefix + orig_output_name
        orig_train_x_y = orig_node.get_train_x_y()
        return WillumpTrainingNode(x_name=x_name, x_node=new_x_node,
                                   y_name=orig_y_name, y_node=orig_y_node,
                                   output_name=new_output_name, train_x_y=orig_train_x_y)

    for node in sorted_nodes:
        if isinstance(node, WillumpTrainingNode):
            training_node: WillumpTrainingNode = node
            break
    else:
        return sorted_nodes
    train_x, train_y = training_node.get_train_x_y()
    feature_importances = calculate_feature_importance(x=train_x, y=train_y,
                                                       train_predict_score_functions=train_predict_score_functions,
                                                       model_inputs=training_node.get_model_inputs())
    training_cascades["feature_importances"] = feature_importances
    indices_to_costs_map = create_indices_to_costs_map(training_node)
    training_cascades["indices_to_costs_map"] = indices_to_costs_map
    more_important_inputs, less_important_inputs = split_model_inputs(training_node, feature_importances,
                                                                      indices_to_costs_map)
    training_input_node = training_node.get_in_nodes()[0]
    # Create Willump graphs and code blocks that produce the more and less important inputs.
    base_discovery_dict = {}
    less_important_inputs_head = graph_from_input_sources(training_input_node, less_important_inputs, typing_map,
                                                          base_discovery_dict, "less")
    less_important_inputs_block = get_model_node_dependencies(less_important_inputs_head, base_discovery_dict)
    base_discovery_dict = {}
    more_important_inputs_head = graph_from_input_sources(training_input_node, more_important_inputs, typing_map,
                                                          base_discovery_dict, "more")
    more_important_inputs_block = get_model_node_dependencies(more_important_inputs_head, base_discovery_dict)
    combiner_node = get_combiner_node(more_important_inputs_head, less_important_inputs_head, training_input_node)
    small_training_node = recreate_training_node(more_important_inputs_head, training_node, "small_")
    big_training_node = recreate_training_node(combiner_node, training_node, "")
    # Store the big model for evaluation.
    big_model_python_name = strip_linenos_from_var(big_training_node.get_output_names()[0])
    add_big_model_python = "%s[\"big_model\"] = %s" % (WILLUMP_TRAINING_CASCADE_NAME, big_model_python_name)
    add_big_model_ast: ast.Module = \
        ast.parse(add_big_model_python, "exec")
    add_big_model_node = WillumpPythonNode(python_ast=add_big_model_ast.body[0], input_names=[big_model_python_name],
                                           output_names=[], output_types=[], in_nodes=[big_training_node])
    # Store the small model for evaluation.
    small_model_python_name = strip_linenos_from_var(small_training_node.get_output_names()[0])
    add_small_model_python = "%s[\"small_model\"] = %s" % (WILLUMP_TRAINING_CASCADE_NAME, small_model_python_name)
    add_small_model_ast: ast.Module = \
        ast.parse(add_small_model_python, "exec")
    add_small_model_node = WillumpPythonNode(python_ast=add_small_model_ast.body[0],
                                             input_names=[small_model_python_name],
                                             output_names=[], output_types=[], in_nodes=[small_training_node])
    base_discovery_dict = {}
    # Remove the original code for creating model inputs to replace with the new code.
    training_dependencies = get_model_node_dependencies(training_input_node, base_discovery_dict)
    for node in training_dependencies:
        sorted_nodes.remove(node)
    # Add all the new code for creating model inputs and training from them.
    training_node_index = sorted_nodes.index(training_node)
    sorted_nodes = sorted_nodes[:training_node_index] + more_important_inputs_block + less_important_inputs_block \
                   + [combiner_node] + [big_training_node, small_training_node,
                                        add_big_model_node, add_small_model_node] \
                   + sorted_nodes[training_node_index + 1:]
    return sorted_nodes


def eval_model_cascade_pass(sorted_nodes: List[WillumpGraphNode],
                            typing_map: MutableMapping[str, WeldType],
                            eval_cascades: dict,
                            cascade_threshold: float,
                            batch: bool,
                            top_k: Optional[int]) -> List[WillumpGraphNode]:
    """
    Take in a program with a model.  Use pre-computed feature importances to partition the models' input
    sources into those more and less important.  Rewrite the program so it first evaluates a pre-trained smaller
    model on all more-important input sources and checks confidence.  If it is above some threshold, use
    that prediction, otherwise, predict with a pre-trained bigger model on all inputs.
    """

    def get_small_model_nodes(orig_model_node: WillumpModelNode, new_input_node: WillumpGraphNode) \
            -> Tuple[WillumpModelNode, WillumpGraphNode]:
        assert (isinstance(orig_model_node, WillumpPredictNode) or
                (top_k is not None and isinstance(orig_model_node, WillumpPredictProbaNode)))
        assert (len(new_input_node.get_output_names()) == 1)
        proba_output_name = "small__proba_" + orig_model_node.get_output_name()
        output_type = WeldVec(WeldDouble())
        typing_map[proba_output_name] = output_type
        new_input_name = new_input_node.get_output_names()[0]
        predict_proba_node = WillumpPredictProbaNode(model_name=SMALL_MODEL_NAME,
                                                     x_name=new_input_name,
                                                     x_node=new_input_node,
                                                     output_name=proba_output_name,
                                                     output_type=output_type)
        threshold_output_name = "small_preds_" + orig_model_node.get_output_name()
        if top_k is None:
            threshold_node = CascadeThresholdProbaNode(input_node=predict_proba_node, input_name=proba_output_name,
                                                       output_name=threshold_output_name,
                                                       threshold=cascade_threshold)
        else:
            threshold_node = CascadeTopKSelectionNode(input_node=predict_proba_node, input_name=proba_output_name,
                                                      output_name=threshold_output_name,
                                                      top_k=top_k)
        typing_map[threshold_output_name] = WeldVec(WeldChar())
        return predict_proba_node, threshold_node

    def get_big_model_nodes(orig_model_node: WillumpModelNode, new_input_node: WillumpGraphNode,
                            small_model_output_node: CascadeThresholdProbaNode, small_model_output_name: str) \
            -> Tuple[WillumpModelNode, CascadeCombinePredictionsNode]:
        assert (isinstance(orig_model_node, WillumpPredictNode) or
                (top_k is not None and isinstance(orig_model_node, WillumpPredictProbaNode)))
        assert (len(new_input_node.get_output_names()) == 1)
        output_name = orig_model_node.get_output_name()
        output_type = orig_model_node.get_output_type()
        new_input_name = new_input_node.get_output_names()[0]
        if top_k is None:
            big_model_output = WillumpPredictNode(model_name=BIG_MODEL_NAME,
                                                  x_name=new_input_name,
                                                  x_node=new_input_node,
                                                  output_name=output_name,
                                                  output_type=output_type,
                                                  input_width=orig_model_node.input_width)
        else:
            big_model_output = WillumpPredictProbaNode(model_name=BIG_MODEL_NAME,
                                                       x_name=new_input_name,
                                                       x_node=new_input_node,
                                                       output_name=output_name,
                                                       output_type=output_type)
        combining_node = CascadeCombinePredictionsNode(big_model_predictions_node=big_model_output,
                                                       big_model_predictions_name=output_name,
                                                       small_model_predictions_node=small_model_output_node,
                                                       small_model_predictions_name=small_model_output_name,
                                                       output_name=output_name,
                                                       output_type=output_type)
        return big_model_output, combining_node

    def get_combiner_node_eval(mi_head: WillumpGraphNode, li_head: WillumpGraphNode, orig_node: WillumpGraphNode,
                               small_model_output_node: CascadeThresholdProbaNode) \
            -> WillumpGraphNode:
        """
        Generate a node that will fuse node_one and node_two to match the output of orig_node.  Requires all
        three nodes be of the same type.
        """
        assert (len(orig_node.get_output_types()) == len(mi_head.get_output_types()) == len(
            li_head.get_output_types()) == 1)
        orig_output_type = orig_node.get_output_types()[0]
        if isinstance(orig_output_type, WeldCSR):
            return CascadeStackSparseNode(more_important_nodes=[mi_head],
                                          more_important_names=[mi_head.get_output_names()[0]],
                                          less_important_nodes=[li_head],
                                          less_important_names=[li_head.get_output_names()[0]],
                                          small_model_output_node=small_model_output_node,
                                          small_model_output_name=small_model_output_node.get_output_name(),
                                          output_name=orig_node.get_output_names()[0],
                                          output_type=orig_output_type)
        elif isinstance(orig_output_type, WeldPandas):
            mi_output_types = mi_head.get_output_types()[0]
            li_output_types = li_head.get_output_types()[0]
            assert (isinstance(mi_output_types, WeldPandas) and isinstance(li_output_types, WeldPandas))
            new_selected_columns = mi_output_types.column_names + li_output_types.column_names
            new_field_types = mi_output_types.field_types + li_output_types.field_types
            output_name = orig_node.get_output_names()[0]
            new_output_type = WeldPandas(column_names=new_selected_columns, field_types=new_field_types)
            typing_map[output_name] = new_output_type
            return CascadeColumnSelectionNode(more_important_nodes=[mi_head],
                                              more_important_names=[mi_head.get_output_names()[0]],
                                              more_important_types=[mi_output_types],
                                              less_important_nodes=[li_head],
                                              less_important_names=[li_head.get_output_names()[0]],
                                              less_important_types=[li_output_types],
                                              output_name=output_name,
                                              small_model_output_node=small_model_output_node,
                                              small_model_output_name=small_model_output_node.get_output_name(),
                                              selected_columns=new_selected_columns)
        elif isinstance(orig_output_type, WeldVec):
            assert (isinstance(orig_output_type.elemType, WeldVec))
            return CascadeStackDenseNode(more_important_nodes=[mi_head],
                                         more_important_names=[mi_head.get_output_names()[0]],
                                         less_important_nodes=[li_head],
                                         less_important_names=[li_head.get_output_names()[0]],
                                         small_model_output_node=small_model_output_node,
                                         small_model_output_name=small_model_output_node.get_output_name(),
                                         output_name=orig_node.get_output_names()[0],
                                         output_type=orig_output_type)
        else:
            panic("Unrecognized eval combiner output type %s" % orig_output_type)

    for node in sorted_nodes:
        if isinstance(node, WillumpModelNode):
            model_node: WillumpModelNode = node
            break
    else:
        return sorted_nodes
    feature_importances = eval_cascades["feature_importances"]
    indices_to_costs_map = eval_cascades["indices_to_costs_map"]
    more_important_inputs, less_important_inputs = split_model_inputs(model_node, feature_importances,
                                                                      indices_to_costs_map)
    model_input_node = model_node.get_in_nodes()[0]
    # Create Willump graphs and code blocks that produce the more important inputs.
    base_discovery_dict = {}
    more_important_inputs_head = graph_from_input_sources(model_input_node, more_important_inputs, typing_map,
                                                          base_discovery_dict, "more")
    more_important_inputs_block = get_model_node_dependencies(more_important_inputs_head, base_discovery_dict)
    # The small model predicts all examples from the more important inputs.
    new_small_model_node, threshold_node = \
        get_small_model_nodes(model_node, more_important_inputs_head)
    small_model_preds_name = threshold_node.get_output_name()
    # Less important inputs are materialized only if the small model lacks confidence in an example.
    base_discovery_dict = {}
    less_important_inputs_head = graph_from_input_sources(model_input_node, less_important_inputs, typing_map,
                                                          base_discovery_dict, "less",
                                                          small_model_output_node=threshold_node)
    less_important_inputs_block = get_model_node_dependencies(less_important_inputs_head, base_discovery_dict,
                                                              small_model_output_node=threshold_node)
    # The big model predicts "hard" (for the small model) examples from all inputs.
    combiner_node = get_combiner_node_eval(more_important_inputs_head, less_important_inputs_head, model_input_node,
                                           threshold_node)
    new_big_model_node, preds_combiner_node = get_big_model_nodes(model_node, combiner_node,
                                                                  threshold_node,
                                                                  small_model_preds_name)
    base_discovery_dict = {}
    # Remove the original code for creating model inputs to replace with the new code.
    training_dependencies = get_model_node_dependencies(model_input_node, base_discovery_dict)
    for node in training_dependencies:
        sorted_nodes.remove(node)
    # Add all the new code for creating model inputs and training from them.
    model_node_index = sorted_nodes.index(model_node)
    small_model_nodes = [new_small_model_node, threshold_node]
    # In a point setting, you can immediately exit if the small model node is confident.
    if batch is False:
        point_early_exit_node = CascadePointEarlyExitNode(small_model_output_node=threshold_node,
                                                          small_model_output_name=small_model_preds_name)
        small_model_nodes.append(point_early_exit_node)
    big_model_nodes = [combiner_node, new_big_model_node, preds_combiner_node]
    sorted_nodes = sorted_nodes[:model_node_index] + more_important_inputs_block + small_model_nodes + \
                   less_important_inputs_block + big_model_nodes + sorted_nodes[model_node_index + 1:]
    return sorted_nodes

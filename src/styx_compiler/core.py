"""
Main Styx transpiler implementation.
"""

import tempfile
from collections.abc import Mapping
from pathlib import Path

import libcst as cst
import mypy.api
from libcst import CSTNode, FlattenSentinel, FunctionDef, Module, RemovalSentinel
from libcst_mypy import MypyTypeInferenceProvider
from libcst_mypy.utils import MypyType

from styx_compiler.config import N_PARTITIONS
from styx_compiler.processor import FunctionProcessor
from styx_compiler.transformers import (
    EntityTypeReplacer,
    InitBodyTransformer,
    RemoteCallLinearizer,
    ReturnHandlerTransformer,
    StateAccessTransformer,
)
from styx_compiler.visitor import EntityDiscoveryVisitor


def _uses_state(node: cst.CSTNode) -> bool:
    """Recursively checks whether any Name('state') appears in the CST subtree."""
    if isinstance(node, cst.Name) and node.value == "state":
        return True
    return any(_uses_state(child) for child in node.children)


class StyxTransformer(cst.CSTTransformer):
    """
    Main transformer that processes entity classes and converts them to Styx operators.
    """

    def __init__(
        self,
        entities: dict[str, str],
        metadata: Mapping,
        entity_keys: dict[str, str] | None = None,
        entity_init_params: dict[str, list[str]] | None = None,
    ):
        super().__init__()
        self.entities = entities
        self.metadata = metadata
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}
        self.current_operator = None
        self.self_attr_types = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if node.name.value in self.entities:
            self.current_operator = node.name.value
            self.self_attr_types = {}
            return True
        return False

    def leave_Module(self, _original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        imports = [
            cst.SimpleStatementLine(body=[cst.parse_statement("import uuid").body[0]]),
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.operator import Operator").body[0]]),
            cst.SimpleStatementLine(
                body=[cst.parse_statement("from styx.common.stateful_function import StatefulFunction").body[0]]
            ),
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.logging import logging").body[0]]),
            cst.EmptyLine(),
        ]

        helpers_code = """
def send_reply(ctx: StatefulFunction, reply_to: list, result):
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], result, reply_to),
        )
    else:
        return result


def push_continuation(
    ctx: StatefulFunction, reply_to: list, op_name: str, fun: str, step_id: str, context: dict
) -> list:
    context_dict = ctx.get_func_context()
    continuation_id = str(uuid.uuid4())
    context_dict[continuation_id] = context
    ctx.put_func_context(context_dict)
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": op_name,
            "fun": fun,
            "id": step_id,
            "context": continuation_id,
        }
    )
    return reply_to


def resolve_context(ctx: StatefulFunction, context_data) -> dict:
    if isinstance(context_data, dict):
        return context_data

    ctx_dict = ctx.get_func_context()
    params = ctx_dict.pop(context_data)
    ctx.put_func_context(ctx_dict)
    return params
"""
        helpers_module = cst.parse_module(helpers_code)
        helpers = [*list(helpers_module.body), cst.EmptyLine()]

        # Filter out stuff for mytype (entity function, logging class) from body
        stub_names = {"entity", "logging"}
        filtered_body = [
            stmt
            for stmt in updated_node.body
            if not (
                (isinstance(stmt, cst.FunctionDef) and stmt.name.value in stub_names)
                or (isinstance(stmt, cst.ClassDef) and stmt.name.value in stub_names)
            )
        ]

        new_body = list(imports) + helpers + filtered_body
        return updated_node.with_changes(body=new_body)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef | cst.FlattenSentinel:
        if original_node.name.value not in self.entities:
            return updated_node

        op_name = self.entities[original_node.name.value]

        op_def_code = f"{op_name}_operator = Operator('{op_name}', n_partitions={N_PARTITIONS})"
        op_def_node = cst.parse_statement(op_def_code)

        new_nodes = [op_def_node, cst.EmptyLine()]

        for statement in updated_node.body.body:
            if isinstance(statement, (cst.FunctionDef, cst.ClassDef)):
                new_nodes.append(statement)
                new_nodes.append(cst.EmptyLine())

        return cst.FlattenSentinel(new_nodes)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> FunctionDef | RemovalSentinel | FlattenSentinel:
        if self.current_operator is None:
            return updated_node

        func_name = original_node.name.value

        if func_name == "__init__":
            return self.transform_init(updated_node)
        if func_name == "__key__":
            return cst.RemoveFromParent()
        return self.transform_method(original_node, updated_node)

    def transform_init(self, node: cst.FunctionDef) -> cst.FunctionDef:
        new_name = cst.Name(value="create")

        ctx_param = cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(annotation=cst.Name("StatefulFunction")))
        reply_to_param = cst.Param(
            name=cst.Name("reply_to"),
            annotation=cst.Annotation(annotation=cst.Name("list")),
            default=cst.Name("None"),
        )
        new_params = [ctx_param] + [p for p in node.params.params if p.name.value != "self"] + [reply_to_param]

        body_transformer = InitBodyTransformer()
        new_body = node.body.visit(body_transformer)

        dict_node = cst.Dict(elements=body_transformer.state_dict_entries)

        put_call = cst.SimpleStatementLine(
            body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name("state"))], value=dict_node)]
        )

        put_state = cst.parse_statement("ctx.put(state)")
        put_func_state = cst.parse_statement("ctx.put_func_context({})")

        return_stmt = cst.parse_statement("return ctx.key")

        new_block = new_body.with_changes(
            body=[*body_transformer.other_statements, put_call, put_state, put_func_state, return_stmt]
        )
        reply_to_transformer = ReturnHandlerTransformer()
        final_block = new_block.visit(reply_to_transformer)

        decorator_name = f"{self.entities[self.current_operator]}_operator"
        decorator = cst.Decorator(decorator=cst.Attribute(value=cst.Name(decorator_name), attr=cst.Name("register")))

        return node.with_changes(
            name=new_name,
            params=node.params.with_changes(params=new_params),
            body=final_block,
            asynchronous=cst.Asynchronous(),
            decorators=[decorator],
        )

    def transform_method(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef | cst.FlattenSentinel:
        # Split function at remote calls
        processor = FunctionProcessor(
            original_node,
            self.current_operator,
            self.entities,
            self.metadata,
            self.entity_keys,
            self.entity_init_params,
        )
        new_functions = processor.process()

        # Post-Process
        final_nodes = []

        for func in new_functions:
            state_transformer = StateAccessTransformer()
            transformed_func = func.visit(state_transformer)

            # Ensure any function that uses `state` loads it from ctx
            if _uses_state(transformed_func):
                get_state = cst.parse_statement("state = ctx.get()")
                transformed_func = transformed_func.with_changes(
                    body=cst.IndentedBlock(body=[get_state, *list(transformed_func.body.body)])
                )

            # Apply Return Handler to all functions
            reply_to_transformer = ReturnHandlerTransformer()
            transformed_func = transformed_func.visit(reply_to_transformer)

            # Finalize original method signature only for the root function
            if transformed_func.name.value == original_node.name.value:
                transformed_func = self._finalize_original_signature(transformed_func, updated_node)

            final_nodes.append(transformed_func)
            final_nodes.append(cst.EmptyLine())

        return cst.FlattenSentinel(final_nodes)

    def _finalize_original_signature(self, node: cst.FunctionDef, reference_node: cst.FunctionDef):
        ctx_param = cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction")))
        reply_to_param = cst.Param(
            name=cst.Name("reply_to"), annotation=cst.Annotation(cst.Name("list")), default=cst.Name("None")
        )

        new_params = (
            [ctx_param] + [p for p in reference_node.params.params if p.name.value != "self"] + [reply_to_param]
        )

        op_name = self.entities[self.current_operator] + "_operator"
        decorator = cst.Decorator(decorator=cst.Attribute(value=cst.Name(op_name), attr=cst.Name("register")))

        return node.with_changes(
            params=node.params.with_changes(params=new_params), decorators=[decorator], asynchronous=cst.Asynchronous()
        )


class StyxTranspiler:
    """
    Main transpiler class that orchestrates the transformation process.
    """

    def __init__(self, source_code: str):
        self.source_code = source_code
        self.cst_tree = cst.parse_module(source_code)
        self.entities: dict[str, str] = {}
        self.entity_keys = None
        self.entity_init_params = None

    def run(self) -> str:
        """
        Run the transpilation process.

        Returns:
            str: The transpiled code
        """
        print("--- Starting Transpilation ---")

        # 1. Discover entities
        visitor = EntityDiscoveryVisitor()
        self.cst_tree.visit(visitor)
        self.entities = visitor.entities
        self.entity_keys = visitor.entity_keys
        self.entity_init_params = visitor.entity_init_params
        print(f"Discovered Entities: {self.entities}")
        print(f"Entity Keys: {self.entity_keys}")
        print(f"Entity Init Params: {self.entity_init_params}")

        # 2. Linearize
        linearizer = RemoteCallLinearizer()
        linearized_tree = self.cst_tree.visit(linearizer)
        linearized_code = linearized_tree.code

        # 3. Run mypy on the linearized code to get type metadata
        module, metadata = StyxTranspiler._resolve_types(linearized_code)
        print(f"Mypy resolved {len(metadata)} type annotations")

        # 4. Transform using the same node tree (metadata lookups match)
        transformer = StyxTransformer(self.entities, metadata, self.entity_keys, self.entity_init_params)
        modified_tree = module.visit(transformer)

        # 5. Replace entity type annotations with key types
        type_replacer = EntityTypeReplacer(self.entity_keys, self.entity_init_params)
        modified_tree = modified_tree.visit(type_replacer)

        return modified_tree.code

    @staticmethod
    def _resolve_types(source_code: str) -> tuple[Module, Mapping[CSTNode, MypyType]]:
        """
        Run mypy on the source code and return (parsed_module, metadata_dict).
        The metadata_dict maps cst.CSTNode -> MypyType.
        """
        # Prepend so mypy does not fail
        stubs = "def entity(cls): return cls\nclass logging:\n    @staticmethod\n    def warning(msg): pass\n"
        full_code = stubs + source_code

        # Write to temp file for mypy to analyze
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(full_code)
            tmp_path = Path(f.name)

        try:
            # Make sure the code is type correct
            stdout, _stderr, exit_code = mypy.api.run([str(tmp_path)])
            if exit_code != 0:
                clean_errs = stdout.replace(str(tmp_path), "source")
                msg = f"Mypy Type Check Failed:\n{clean_errs}"
                raise RuntimeError(msg)

            # Generate mypy cache for semantic analysis
            cache = MypyTypeInferenceProvider.gen_cache(
                root_path=tmp_path.parent,
                paths=[str(tmp_path)],
            )

            # Parse the same code with MetadataWrapper and resolve types
            module = cst.parse_module(full_code)
            file_cache = cache.get(str(tmp_path))
            wrapper = cst.metadata.MetadataWrapper(
                module,
                unsafe_skip_copy=True,
                cache={MypyTypeInferenceProvider: file_cache},
            )
            metadata = wrapper.resolve(MypyTypeInferenceProvider)
        finally:
            tmp_path.unlink(missing_ok=True)

        return wrapper.module, metadata


# Main execution
def main():
    file_name = "user_item.py"
    input_file = "./examples/original/" + file_name
    output_file = "./examples/compiled/" + file_name

    try:
        with open(input_file, encoding="utf-8") as f:
            code = f.read()
    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
        exit(1)

    transpiler = StyxTranspiler(code)
    output_code = transpiler.run()

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_code)

    print(f"Successfully transpiled '{input_file}' to '{output_file}'")

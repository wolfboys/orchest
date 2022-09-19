"""Module about pipeline definition/de-serialization and pipeline runs.

Essentially, it covers:
- transforming a pipeline definition, e.g. obtained by the pipeline
    json, into an instance of the Pipeline class, which adds some nice
    to have logic.
- transforming said Pipeline instance to a valid k8s workflow
    definition, where the pipeline is run as an argo workflow.
- the required function to actually perform a pipeline run.

As a client of this module you are most likely interested in how to get
a pipeline json to a Pipeline instance (point 1) and how to use that to
perform a pipeline run (point 3), with "run_pipeline_workflow".

"""
import asyncio
import copy
import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Literal, Optional, Set

import aiohttp
from celery.contrib.abortable import AbortableAsyncResult

from _orchest.internals import config as _config
from _orchest.internals.utils import get_step_and_kernel_volumes_and_volume_mounts
from app.connections import k8s_core_api, k8s_custom_obj_api
from app.core import pod_scheduling
from app.types import (
    PipelineDefinition,
    PipelineProperties,
    PipelineStepProperties,
    RunConfig,
)
from app.utils import get_logger
from config import CONFIG_CLASS

logger = get_logger()


def construct_pipeline(
    uuids: Iterable[str],
    run_type: str,
    pipeline_definition: PipelineDefinition,
    **kwargs,
) -> "Pipeline":
    """Constructs a pipeline from a description with selection criteria.

    Based on the run type and selection of UUIDs, constructs the
    appropriate Pipeline.

    TODO:
        Include config options to be based to methods. This can be done
        via the **kwargs option.

        Example: waiting on container completion, or inclusive or
            exclusive of the selection for "incoming" `run_type`.

        All options for the config should be documented somewhere.

    Args:
        uuids: a selection/sequence of pipeline step UUIDs. If
            `run_type` equals "full", then this argument is ignored.
        run_type: one of ("full", "selection", "incoming").
        pipeline_definition: a json description of the pipeline.
        config: configuration for the `run_type`.

    Returns:
        Always returns a Pipeline. Depending on the `run_type` the
        Pipeline is constructed as follows from the given
        `pipeline_definition`:
            * "full" -> entire pipeline from description
            * "selection" -> induced subgraph based on selection.
            * "incoming" -> all incoming steps of the selection. In
                other words: all ancestors of the steps of the
                selection.

        As of now, the selection itself is NOT included in the Pipeline
        if `run_type` equals "incoming".

    Raises:
        ValueError if the `run_type` is incorrectly specified.
    """
    # Create a pipeline from the pipeline_definition. And run the
    # appropriate method based on the run_type.
    pipeline = Pipeline.from_json(pipeline_definition)

    if run_type == "full":
        return pipeline

    if run_type == "selection":
        return pipeline.get_induced_subgraph(uuids)

    if run_type == "incoming":
        return pipeline.incoming(uuids, inclusive=False)

    raise ValueError("Function not defined for specified run_type")


async def update_status(
    status: str,
    task_id: str,
    session: aiohttp.ClientSession,
    type: Literal["step", "pipeline"],
    run_endpoint: str,
    uuid: Optional[str] = None,
) -> Any:
    """Updates status of `type` via the orchest-api.

    Args:
        type: One of ``['pipeline', 'step']``.
    """
    data = {"status": status}
    if data["status"] == "STARTED":
        data["started_time"] = datetime.utcnow().isoformat()
    elif data["status"] in ["SUCCESS", "FAILURE"]:
        data["finished_time"] = datetime.utcnow().isoformat()

    base_url = f"{CONFIG_CLASS.ORCHEST_API_ADDRESS}/{run_endpoint}/{task_id}"

    if type == "step":
        url = f"{base_url}/{uuid}"

    elif type == "pipeline":
        url = base_url

    else:
        raise ValueError("Given `type` of '{type}' is not valid.")

    # Just await the response. The proposed fix on the aiohttp GitHub to
    # do `response.json(content_type=None)` still results in parsing
    # issues.
    await session.put(url, json=data)


class PipelineStep:
    """A step of a pipeline.

    It can also be thought of as a node of a graph.

    Args:
        properties: properties of the step used for execution.
        parents: the parents/incoming steps of the current step.

    Attributes:
        properties: see "Args" section.
        parents: see "Args" section.
    """

    def __init__(
        self,
        properties: PipelineStepProperties,
        parents: Optional[List["PipelineStep"]] = None,
    ) -> None:
        self.properties = properties
        self.parents = parents if parents is not None else []

        # Keeping a list of children allows us to traverse the pipeline
        # also in the other direction. This is helpful for certain
        # Pipeline methods.
        self._children: List["PipelineStep"] = []

    def __eq__(self, other) -> bool:
        return self.properties["uuid"] == other.properties["uuid"]

    def __hash__(self) -> int:
        return hash(self.properties["uuid"])

    def __str__(self) -> str:
        if self.properties:
            return f'<PipelineStep: {self.properties["title"]}>'

        return "<Pipelinestep: None>"

    def __repr__(self) -> str:
        # TODO: This is actually not correct: it should be
        #       self.properties. But this just look ugly as hell
        #       (so maybe for later). And strictly, should also include
        #       its parents.
        if self.properties:
            return f'PipelineStep({self.properties["title"]!r})'

        return "Pipelinestep(None)"


class Pipeline:
    def __init__(
        self, steps: List[PipelineStep], properties: PipelineProperties
    ) -> None:
        self.steps = steps

        # We want to be able to serialize a Pipeline back to a json
        # file. Therefore we would need to store the Pipeline name and
        # UUID from the json first.
        self.properties: PipelineProperties = properties  # type: ignore

        # See the sentinel property for explanation.
        self._sentinel: Optional[PipelineStep] = None

    @classmethod
    def from_json(cls, description: PipelineDefinition) -> "Pipeline":
        """Constructs a pipeline from a json description.

        This is an alternative constructur.

        Args:
            description: json description of Pipeline.

        Returns:
            A pipeline object defined by the given description.
        """
        # Create a mapping for all the steps from UUID to object.
        steps = {
            uuid: PipelineStep(properties)
            for uuid, properties in description["steps"].items()
        }

        # For every step populate its parents and _children attributes.
        for step in steps.values():
            for uuid in step.properties["incoming_connections"]:
                step.parents.append(steps[uuid])
                steps[uuid]._children.append(step)

        properties: PipelineProperties = {
            "name": description["name"],
            "uuid": description["uuid"],
            "settings": description["settings"],
            "parameters": description.get("parameters", {}),
            "services": description.get("services", {}),
            "version": description.get("version"),
        }
        return cls(list(steps.values()), properties)

    def to_dict(self) -> PipelineDefinition:
        """Convert the Pipeline to its dictionary description."""
        description: PipelineDefinition = {"steps": {}}
        for step in self.steps:
            description["steps"][step.properties["uuid"]] = step.properties

        description.update(self.properties)
        return description

    def get_step(self, uuid: str) -> PipelineStep:
        # NOTE: This is slow, although reasonable for small/medium size
        # pipelines.
        for step in self.steps:
            if uuid == step.properties["uuid"]:
                return step
        else:
            raise ValueError(f"Step with uuid '{uuid}' not in pipeline.")

    def get_environments(self) -> Set[str]:
        """Returns the set of UUIDs of the used environments.

        Returns:
            Set of environments uuids used among the pipeline steps and
            services making use of orchest environments.

        """
        st_envs = set([step.properties["environment"] for step in self.steps])
        prefix = _config.ENVIRONMENT_AS_SERVICE_PREFIX
        sr_envs = set(
            [
                sr["image"].replace(prefix, "")
                for sr in self.properties.get("services", {}).values()
                if sr["image"].startswith(prefix)
            ]
        )

        return set.union(st_envs, sr_envs)

    def get_params(self) -> Dict[str, Any]:
        return self.properties.get("parameters", {})

    def get_induced_subgraph(self, selection: Iterable[str]) -> "Pipeline":
        """Returns a new pipeline whos set of steps equal the selection.

        Takes an induced subgraph of the pipeline formed by a subset of
        its steps given by the selection (of UUIDs).

        Example:
            When the selection consists of: a --> b. Then it is
            important that "a" is run before "b". Therefore the induced
            subgraph has to be taken to ensure the correct ordering,
            instead of executing the steps independently (and in
            parallel).

        Args:
            selection: list of UUIDs representing `PipelineStep`s.

        Returns:
            An induced pipeline by the set of steps (defined by the
            given selection).
        """
        keep_steps = [
            step for step in self.steps if step.properties["uuid"] in selection
        ]

        # Only keep connection to parents and children if these steps
        # are also included in the selection. In addition, to keep
        # consistency of the properties attributes of the steps, we
        # update the "incoming_connections" to be representative of the
        # new pipeline structure.
        new_steps = []
        for step in keep_steps:
            # Take a deepcopy such that the properties of the new and
            # original step do not point to the same object (since we
            # want to update the "incoming_connections").
            new_step = PipelineStep(copy.deepcopy(step.properties))
            new_step.parents = [s for s in step.parents if s in keep_steps]
            new_step._children = [s for s in step._children if s in keep_steps]
            new_step.properties["incoming_connections"] = [
                s.properties["uuid"] for s in new_step.parents
            ]
            new_steps.append(new_step)

        properties = copy.deepcopy(self.properties)
        return Pipeline(steps=new_steps, properties=properties)

    def convert_to_induced_subgraph(self, selection: List[str]) -> None:
        """Converts the pipeline to a subpipeline.

        NOTE:
            Exactly the same as `get_induced_subgraph` except that it
            modifies the underlying `Pipeline` object inplace.
        """
        self.steps = [
            step for step in self.steps if step.properties["uuid"] in selection
        ]

        # Removing connection from steps to "non-existing" steps, i.e.
        # steps that are not included in the selection.
        for step in self.steps:
            step.parents = [s for s in step.parents if s in self.steps]
            step._children = [s for s in step._children if s in self.steps]

    def incoming(self, selection: Iterable[str], inclusive: bool = False) -> "Pipeline":
        """Returns a new Pipeline of all ancestors of the selection.

        NOTE:
            The following can be thought of as an edge case. Lets say
            you have the pipeline: a --> b --> c and a selection of
            [b, c] with `inclusive` set to False. Then only step "a"
            would be run.

        Args:
            selection: list of UUIDs representing `PipelineStep`s.
            inclusive: if True, then the steps in the selection are also
                part of the returned `Pipeline`, else the steps will not
                be included.

        Returns:
            An induced pipeline by the set of steps (defined by the
            given selection).
        """
        # This set will be populated with all the steps that are
        # ancestors of the sets given by the selection. Depending on the
        # kwarg `inclusive` the steps from the selection itself will
        # either be included or excluded.
        steps = set()

        # Essentially a BFS where its stack gets initialized with
        # multiple root nodes.
        stack = [step for step in self.steps if step.properties["uuid"] in selection]

        while stack:
            step = stack.pop()
            if step in steps:
                continue

            # Create a new Pipeline step that is a copy of the step. For
            # consistency also update the properties attribute and make
            # it point to a new object.
            new_properties = copy.deepcopy(step.properties)
            new_properties["incoming_connections"] = [
                s.properties["uuid"] for s in step.parents
            ]
            new_step = PipelineStep(new_properties, step.parents)

            # NOTE: the childrens list has to be updated, since the
            # sentinel node uses its information to be computed. On the
            # other hand, the parents, do not change and are always all
            # included.
            new_step._children = [
                s
                for s in step._children
                if s in steps or s.properties["uuid"] in selection
            ]
            steps.add(new_step)
            stack.extend(new_step.parents)

        # Remove steps if the selection should not be included in the
        # new pipeline.
        if inclusive:
            steps_to_be_included = steps
        else:
            steps_to_be_included = steps - set(
                step for step in self.steps if step.properties["uuid"] in selection
            )

            # We have to go over the children again to make sure they
            # also do not include any steps of the selection.
            for step in steps_to_be_included:
                step._children = [
                    s for s in step._children if s in steps_to_be_included
                ]

        properties = copy.deepcopy(self.properties)
        return Pipeline(steps=list(steps_to_be_included), properties=properties)

    def __repr__(self) -> str:
        return f"Pipeline({self.steps!r})"


def _step_to_workflow_manifest_task(step: PipelineStep, run_config: RunConfig) -> dict:
    # The working directory is the location of the file being
    # executed.
    project_relative_file_path = os.path.join(
        os.path.split(run_config["pipeline_path"])[0], step.properties["file_path"]
    )
    working_dir = os.path.split(project_relative_file_path)[0]

    user_env_variables = [
        {"name": key, "value": str(value)}
        for key, value in run_config["user_env_variables"].items()
    ]
    orchest_env_variables = [
        {"name": "ORCHEST_STEP_UUID", "value": step.properties["uuid"]},
        {"name": "ORCHEST_SESSION_UUID", "value": run_config["session_uuid"]},
        {"name": "ORCHEST_SESSION_TYPE", "value": run_config["session_type"]},
        {"name": "ORCHEST_PIPELINE_UUID", "value": run_config["pipeline_uuid"]},
        {"name": "ORCHEST_PIPELINE_PATH", "value": _config.PIPELINE_FILE},
        {"name": "ORCHEST_PROJECT_UUID", "value": run_config["project_uuid"]},
        {"name": "ORCHEST_NAMESPACE", "value": _config.ORCHEST_NAMESPACE},
        {"name": "ORCHEST_CLUSTER", "value": _config.ORCHEST_CLUSTER},
    ]
    # Note that the order of concatenation matters, so that there is no
    # risk that the user overwrites internal variables accidentally.
    env_variables = user_env_variables + orchest_env_variables

    # Need to reference the ip because the local docker engine will run
    # the container, and if the image is missing it will prompt a pull
    # which will fail because the FQDN can't be resolved by the local
    # engine on the node. K8S_TODO: fix this.
    registry_ip = k8s_core_api.read_namespaced_service(
        _config.REGISTRY, _config.ORCHEST_NAMESPACE
    ).spec.cluster_ip
    # The image of the step is the registry address plus the image name.
    image = (
        registry_ip
        + "/"
        + run_config["env_uuid_to_image"][step.properties["environment"]]
    )

    if CONFIG_CLASS.SINGLE_NODE:
        # NOTE: In the single-node case we don't run an initContainer to
        # pre-pull images.
        task = {
            # "Name cannot begin with a digit when using either
            # 'depends' or 'dependencies'".
            "name": f'step-{step.properties["uuid"]}',
            "dependencies": [
                f'step-{pstep.properties["uuid"]}' for pstep in step.parents
            ],
            "restartPolicy": "Never",
            # NOTE: Should never need to pull given that the pipeline
            # run is scheduled on the only node in the cluster (which
            # thus has the image).
            "imagePullPolicy": "IfNotPresent",
            "env": env_variables,
            "image": image,
            "command": [
                "/orchest/bootscript.sh",
                "runnable",
                working_dir,
                project_relative_file_path,
            ],
            "resources": {"requests": {"cpu": _config.USER_CONTAINERS_CPU_SHARES}},
        }
    else:
        # This allows us to edit the container that argo runs for us.
        pod_spec_patch = json.dumps(
            {
                "terminationGracePeriodSeconds": 1,
                "containers": [
                    {
                        # NOTE: The `imagePullPolicy` is already set
                        # through the `orchest_values.yml` file for
                        # Argo.
                        "name": "main",
                        "env": env_variables,
                        "restartPolicy": "Never",
                    }
                ],
            },
        )

        task = {
            # "Name cannot begin with a digit when using either
            # 'depends' or 'dependencies'".
            "name": f'step-{step.properties["uuid"]}',
            "dependencies": [
                f'step-{pstep.properties["uuid"]}' for pstep in step.parents
            ],
            "template": "step",
            "arguments": {
                "parameters": [
                    {
                        # Used to keep track of the step when getting
                        # workflow status, since the name we have set is
                        # not reliable, argo will change it.
                        "name": "step_uuid",
                        "value": step.properties["uuid"],
                    },
                    {
                        "name": "image",
                        "value": image,
                    },
                    {"name": "working_dir", "value": working_dir},
                    {
                        "name": "project_relative_file_path",
                        "value": project_relative_file_path,
                    },
                    {"name": "pod_spec_patch", "value": pod_spec_patch},
                    {
                        # NOTE: only used by tests.
                        "name": "tests_uuid",
                        "value": step.properties["uuid"],
                    },
                ]
            },
        }

    return task


def _get_pipeline_argo_templates(
    entrypoint_name: str,
    volume_mounts: List[dict],
    pipeline: Pipeline,
    run_config: RunConfig,
) -> List[Dict[str, Any]]:
    # !Note that modify_pipeline_scheduling_behaviour relies on the
    # structure and the content of the manifest to inject changes,
    # verify that you aren't breaking anything when changing thins here.
    # TODO: add tests for this.
    # https://argoproj.github.io/argo-workflows/fields/#template
    if CONFIG_CLASS.SINGLE_NODE:
        templates = [
            {
                "name": entrypoint_name,
                "failFast": True,
                "retryStrategy": {"limit": "0", "backoff": {"maxDuration": "0s"}},
                "podSpecPatch": json.dumps(
                    {
                        "terminationGracePeriodSeconds": 1,
                    }
                ),
                # Security context for the Pod in which the containers
                # of the containerSet run.
                "securityContext": {
                    "runAsUser": 0,
                    "runAsGroup": int(os.environ.get("ORCHEST_HOST_GID", "1")),
                    "fsGroup": int(os.environ.get("ORCHEST_HOST_GID", "1")),
                },
                # NOTE: Argo only allows a "dag" or "steps" template to
                # reference another template, thus we just create
                # containerSpecs here.
                "containerSet": {
                    "retryStrategy": {"retries": 0},
                    "volumeMounts": volume_mounts,
                    "containers": [
                        _step_to_workflow_manifest_task(step, run_config)
                        for step in pipeline.steps
                    ],
                },
            },
        ]

    else:
        # The first entry of this list is the definition of the DAG,
        # while the second entry is the step definition.
        templates = [
            {
                "name": entrypoint_name,
                "retryStrategy": {"limit": "0", "backoff": {"maxDuration": "0s"}},
                "dag": {
                    "failFast": True,
                    "tasks": [
                        _step_to_workflow_manifest_task(step, run_config)
                        for step in pipeline.steps
                    ],
                },
            },
            {
                # ! The name is important for the logic that alters the
                # scheduling behaviour.
                "name": "step",
                "securityContext": {
                    "runAsUser": 0,
                    "runAsGroup": int(os.environ.get("ORCHEST_HOST_GID", "1")),
                    "fsGroup": int(os.environ.get("ORCHEST_HOST_GID", "1")),
                },
                "inputs": {
                    "parameters": [
                        {"name": param}
                        for param in [
                            "step_uuid",
                            "image",
                            "working_dir",
                            "project_relative_file_path",
                            "pod_spec_patch",
                            "tests_uuid",
                        ]
                    ]
                },
                "retryStrategy": {"limit": "0", "backoff": {"maxDuration": "0s"}},
                "container": {
                    "image": "{{inputs.parameters.image}}",
                    "command": [
                        "/orchest/bootscript.sh",
                        "runnable",
                        "{{inputs.parameters.working_dir}}",
                        "{{inputs.parameters.project_relative_file_path}}",
                    ],
                    "volumeMounts": volume_mounts,
                    "resources": {
                        "requests": {"cpu": _config.USER_CONTAINERS_CPU_SHARES}
                    },
                },
                "podSpecPatch": "{{inputs.parameters.pod_spec_patch}}",
            },
        ]

    return templates


def _pipeline_to_workflow_manifest(
    session_uuid: str,
    workflow_name: str,
    pipeline: Pipeline,
    run_config: RunConfig,
) -> dict:
    volumes, volume_mounts = get_step_and_kernel_volumes_and_volume_mounts(
        userdir_pvc=run_config["userdir_pvc"],
        project_dir=run_config["project_dir"],
        pipeline_file=run_config["pipeline_path"],
        container_project_dir=_config.PROJECT_DIR,
        container_pipeline_file=_config.PIPELINE_FILE,
        container_runtime_socket=_config.CONTAINER_RUNTIME_SOCKET,
    )

    # these parameters will be fed by _step_to_workflow_manifest_task
    entrypoint_name = "pipeline"
    manifest = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "name": workflow_name,
            "labels": {
                "project_uuid": run_config["project_uuid"],
                "session_uuid": session_uuid,
            },
        },
        "spec": {
            "entrypoint": entrypoint_name,
            "volumes": volumes,
            # The celery task actually takes care of deleting the
            # workflow, this is just a failsafe.
            "ttlStrategy": {
                "secondsAfterCompletion": 1000,
                "secondsAfterSuccess": 1000,
                "secondsAfterFailure": 1000,
            },
            # NOTE: It would be "better" to set `dnsPolicy` to `None`
            # and specify the `search` directive in the `dnsConfig`
            # as to only search within the namespace that Orchest is
            # installed (and then on the internet). This reduced the
            # number of DNS queries, since we know that these are the
            # only two valid options when doing pipeline runs.
            "dnsPolicy": "ClusterFirst",
            "dnsConfig": {
                "options": [
                    {"name": "timeout", "value": "10"},  # 30 is max
                    {"name": "attempts", "value": "5"},  # 5 is max
                ],
            },
            "restartPolicy": "Never",
            "templates": _get_pipeline_argo_templates(
                entrypoint_name=entrypoint_name,
                volume_mounts=volume_mounts,
                pipeline=pipeline,
                run_config=run_config,
            ),
        },
    }
    pod_scheduling.modify_pipeline_scheduling_behaviour(
        run_config["session_type"], manifest
    )
    return manifest


def _is_step_allowed_to_run(
    step: PipelineStep,
    steps_to_finish: Set[PipelineStep],
) -> bool:
    """Returns whether the given step is allowed to run.

    Args:
        step: The step we are checking the run requirements for.
        steps_to_finish: The steps, part of a pipeline run, that have
            not yet finished (or started) executing.

    Returns:
        Have all incoming steps completed?

    """
    return all(s.properties["uuid"] not in steps_to_finish for s in step.parents)


async def run_pipeline_workflow(
    session_uuid: str, task_id: str, pipeline: Pipeline, *, run_config: RunConfig
):
    async with aiohttp.ClientSession() as session:

        await update_status(
            "STARTED",
            task_id,
            session,
            type="pipeline",
            run_endpoint=run_config["run_endpoint"],
        )

        namespace = _config.ORCHEST_NAMESPACE

        steps_to_start = {step.properties["uuid"] for step in pipeline.steps}
        steps_to_finish = set(steps_to_start)
        had_failed_steps = False
        try:
            manifest = _pipeline_to_workflow_manifest(
                session_uuid, f"pipeline-run-task-{task_id}", pipeline, run_config
            )
            k8s_custom_obj_api.create_namespaced_custom_object(
                "argoproj.io", "v1alpha1", namespace, "workflows", body=manifest
            )

            while steps_to_finish:
                # Note: not async.
                resp = k8s_custom_obj_api.get_namespaced_custom_object(
                    "argoproj.io",
                    "v1alpha1",
                    namespace,
                    "workflows",
                    f"pipeline-run-task-{task_id}",
                )
                workflow_nodes: dict = resp.get("status", {}).get("nodes", {})
                for argo_node in workflow_nodes.values():
                    if CONFIG_CLASS.SINGLE_NODE:
                        if argo_node.get("type", "") != "Container":
                            continue

                        # Argo doesn't allow to work with templates for
                        # a containerSet. Thus we fall back to the name
                        # we gave to steps, which includes its uuid.
                        step_uuid = argo_node.get("displayName")
                        if step_uuid is None:
                            # Should never happen.
                            raise Exception(
                                "Did not find `displayName` in Argo workflow node:"
                                f" {argo_node}."
                            )
                        else:
                            step_uuid = step_uuid.replace("step-", "")

                    else:
                        # The nodes includes the entire "pipeline" node
                        if argo_node["templateName"] != "step":
                            continue
                        # The step was not run because the workflow
                        # failed.
                        if "inputs" not in argo_node:
                            continue
                        if argo_node.get("type", "") != "Pod":
                            continue

                        for param in argo_node["inputs"]["parameters"]:
                            if param["name"] == "step_uuid":
                                step_uuid = param["value"]
                                break
                        else:
                            # Should never happen.
                            raise Exception(
                                "Did not find `step_uuid` in parameters of Argo node:"
                                f" {argo_node}."
                            )

                    pipeline_step = pipeline.get_step(step_uuid)
                    argo_node_status = argo_node["phase"]
                    argo_node_message = argo_node.get("message", "")
                    step_status_update = None

                    # Argo does not fail a step if the container is
                    # stuck in a waiting state. Doesn't look like the
                    # pull backoff behavior can be tuned.
                    if argo_node_status in ["Pending", "Running"] and (
                        "ImagePullBackOff" in argo_node_message
                        or "ErrImagePull" in argo_node_message
                    ):
                        step_status_update = "FAILURE"

                    elif (
                        argo_node_status == "Running"
                        and step_uuid in steps_to_start
                        # Strictly speaking only needed in single-node
                        # context as otherwise Argo takes care of
                        # correctly putting a Step in "Running".
                        and _is_step_allowed_to_run(pipeline_step, steps_to_finish)
                    ):
                        step_status_update = "STARTED"
                        steps_to_start.remove(step_uuid)

                    elif (
                        argo_node_status in ["Succeeded", "Failed", "Error"]
                        and step_uuid in steps_to_finish
                    ):
                        step_status_update = {
                            "Succeeded": "SUCCESS",
                            "Failed": "FAILURE",
                            "Error": "FAILURE",
                        }[argo_node_status]

                    if step_status_update is not None:
                        if step_status_update == "FAILURE":
                            had_failed_steps = True

                        if step_status_update in ["FAILURE", "ABORTED", "SUCCESS"]:
                            steps_to_finish.remove(step_uuid)
                            if step_uuid in steps_to_start:
                                steps_to_start.remove(step_uuid)

                        await update_status(
                            step_status_update,
                            task_id,
                            session,
                            type="step",
                            run_endpoint=run_config["run_endpoint"],
                            uuid=step_uuid,
                        )

                if not steps_to_finish or had_failed_steps:
                    break

                if AbortableAsyncResult(task_id).is_aborted():
                    break

                async with session.get(
                    f'{CONFIG_CLASS.ORCHEST_API_ADDRESS}/{run_config["run_endpoint"]}'
                    f"/{task_id}"
                ) as response:
                    run_status = await response.json()
                    # Might not be there if it has been deleted.
                    if run_status.get("status", "ABORTED") in [
                        "SUCCESS",
                        "FAILURE",
                        "ABORTED",
                    ]:
                        break

                await asyncio.sleep(0.25)

            for step_uuid in steps_to_finish:
                await update_status(
                    "ABORTED",
                    task_id,
                    session,
                    type="step",
                    run_endpoint=run_config["run_endpoint"],
                    uuid=step_uuid,
                )

            pipeline_status = "SUCCESS" if not had_failed_steps else "FAILURE"
            await update_status(
                pipeline_status,
                task_id,
                session,
                type="pipeline",
                run_endpoint=run_config["run_endpoint"],
            )

        except Exception as e:
            logger.error(e)
            for step_uuid in steps_to_finish:
                await update_status(
                    "ABORTED",
                    task_id,
                    session,
                    type="step",
                    run_endpoint=run_config["run_endpoint"],
                    uuid=step_uuid,
                )
            await update_status(
                "FAILURE",
                task_id,
                session,
                type="pipeline",
                run_endpoint=run_config["run_endpoint"],
            )

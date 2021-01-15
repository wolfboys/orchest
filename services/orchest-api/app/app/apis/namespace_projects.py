"""API endpoint to manage projects.

Despite the fact that the orchest api has no model related to a
project, a good amount of other models depend on such a concept.
"""
from flask_restx import Namespace, Resource

import app.models as models
from _orchest.internals.two_phase_executor import TwoPhaseExecutor, TwoPhaseFunction
from app.apis.namespace_environment_images import DeleteProjectEnvironmentImages
from app.apis.namespace_experiments import DeleteExperiment
from app.apis.namespace_runs import AbortPipelineRun
from app.apis.namespace_sessions import StopInteractiveSession
from app.connections import db
from app.utils import register_schema

api = Namespace("projects", description="Managing Projects")
api = register_schema(api)


@api.route("/<string:project_uuid>")
@api.param("project_uuid", "UUID of the project")
class Project(Resource):
    @api.doc("delete_project")
    @api.response(200, "Project deleted")
    def delete(self, project_uuid):
        """Delete a project.

        Any session, run, experiment related to the project is stopped
        and removed from the db. Environment images are removed.
        """
        try:
            with TwoPhaseExecutor(db.session) as tpe:
                DeleteProject(tpe).transaction(project_uuid)

        except Exception as e:
            return {"message": str(e)}, 500

        return {"message": "Project deletion was successful."}, 200


class DeleteProject(TwoPhaseFunction):
    """Delete a project and all related entities.


    Project sessions, runs and experiments are stopped. Every
    related entity in the db is removed. Environment images are
    deleted up.
    """

    def transaction(self, project_uuid: str):
        # Any interactive run related to the project is stopped if
        # if necessary, then deleted.
        interactive_runs = (
            models.InteractivePipelineRun.query.filter_by(project_uuid=project_uuid)
            .filter(models.InteractivePipelineRun.status.in_(["PENDING", "STARTED"]))
            .all()
        )
        for run in interactive_runs:
            AbortPipelineRun(self.tpe).transaction(run.run_uuid)
            # Will delete cascade interactive run pipeline step,
            # interactive run image mapping.
            db.session.delete(run)

        # Stop (and delete) any interactive session related to the
        # project.
        sessions = (
            models.InteractiveSession.query.filter_by(
                project_uuid=project_uuid,
            )
            .with_entities(
                models.InteractiveSession.project_uuid,
                models.InteractiveSession.pipeline_uuid,
            )
            .distinct()
            .all()
        )
        for session in sessions:
            # Stop any interactive session related to the pipeline.
            StopInteractiveSession(self.tpe).transaction(
                project_uuid, session.pipeline_uuid
            )

        # Any experiment related to the pipeline is stopped if necessary
        # , then deleted.
        experiments = (
            models.Experiment.query.filter_by(
                project_uuid=project_uuid,
            )
            .with_entities(models.Experiment.experiment_uuid)
            .all()
        )
        for experiment in experiments:
            DeleteExperiment(experiment.experiment_uuid)

        # Remove images related to the project.
        DeleteProjectEnvironmentImages(self.tpe).transaction(project_uuid)

    def collateral(self):
        pass

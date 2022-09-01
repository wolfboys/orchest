import { useGlobalContext } from "@/contexts/GlobalContext";
import { useGetJobData } from "@/jobs-view/hooks/useGetJobData";
import { useReadParameterStrategyFile } from "@/jobs-view/hooks/useReadParameterStrategyFile";
import { useEditJob } from "@/jobs-view/stores/useEditJob";
import { StrategyJson } from "@/types";
import { generateStrategyJson } from "@/utils/webserver-utils";
import { hasValue } from "@orchest/lib-utils";
import React from "react";

/**
 * Loads parameter strategy to `jobChanges.strategy_json` if it is empty, i.e. `{}`.
 * First it will attempts to read the sidecar file, e.g. `main.parameters.json` next to `main.orchest`.
 * If the file is not available, it will then generate a default strategy based on the existing parameters
 * in the pipeline.
 *
 * This hook also returns `readParameterStrategyFile` that can read a file path to load a desired strategy.
 */
export const useLoadParameterStrategy = (): {
  readParameterStrategyFile: (
    path: string
  ) => Promise<StrategyJson | undefined>;
} => {
  const { config } = useGlobalContext();
  const reservedKey = config?.PIPELINE_PARAMETERS_RESERVED_KEY;

  const jobData = useGetJobData();
  const pipelineJson = jobData?.pipeline_definition;

  const setJobChanges = useEditJob((state) => state.setJobChanges);

  const readParameterStrategyFile = useReadParameterStrategyFile();

  const loadParameterStrategyToJobChanges = React.useCallback(async () => {
    if (!jobData || !pipelineJson) return;

    const strategyFromFile = await readParameterStrategyFile();

    const hasLoadedStrategyIntoJobChanges = hasValue(strategyFromFile);

    if (!hasLoadedStrategyIntoJobChanges) {
      setJobChanges({
        strategy_json: generateStrategyJson(pipelineJson, reservedKey),
      });
    }
  }, [
    reservedKey,
    jobData,
    readParameterStrategyFile,
    pipelineJson,
    setJobChanges,
  ]);

  const isDraftJobWithNoParameters = useEditJob(
    (state) =>
      state.jobChanges?.status === "DRAFT" &&
      Object.keys(state.jobChanges?.strategy_json).length === 0
  );

  const loadedStrategyFilePath = useEditJob(
    (state) => state.jobChanges?.loadedStrategyFilePath
  );

  React.useEffect(() => {
    if (isDraftJobWithNoParameters && !hasValue(loadedStrategyFilePath)) {
      loadParameterStrategyToJobChanges();
    }
  }, [
    loadedStrategyFilePath,
    loadParameterStrategyToJobChanges,
    isDraftJobWithNoParameters,
  ]);

  return { readParameterStrategyFile };
};

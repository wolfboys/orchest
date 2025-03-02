import { useEscapeToBlur } from "@/hooks/useEscapeToBlur";
import { useSocketIO } from "@/pipeline-view/hooks/useSocketIO";
import { EnvironmentImageBuild } from "@/types";
import Box from "@mui/material/Box";
import React from "react";
import { FitAddon } from "xterm-addon-fit";
import { XTerm } from "xterm-for-react";
import { ImageBuildStatus } from "./ImageBuildStatus";

type ImageBuildLogProps = {
  build?: EnvironmentImageBuild;
  ignoreIncomingLogs: boolean;
  socketIONamespace?: string;
  streamIdentity: string | undefined;
  hideDefaultStatus?: boolean;
};

export const ImageBuildLog = ({
  build,
  ignoreIncomingLogs,
  socketIONamespace = "",
  streamIdentity,
  hideDefaultStatus,
}: ImageBuildLogProps) => {
  const fitAddon = React.useMemo(() => new FitAddon(), []);
  const xtermRef = React.useRef<XTerm | null>(null);

  const fitTerminal = React.useCallback(() => {
    if (xtermRef.current?.terminal.element?.offsetParent !== null) {
      setTimeout(() => {
        try {
          fitAddon.fit();
        } catch {
          console.warn(
            "fitAddon.fit() failed - Xterm only allows fit when element is visible."
          );
        }
      });
    }
  }, [fitAddon, xtermRef]);

  const printLogs = React.useCallback((logs: string | undefined) => {
    const lines = (logs || "").split("\n");
    for (let x = 0; x < lines.length; x++) {
      if (x > 0) xtermRef.current?.terminal.write("\n\r");
      xtermRef.current?.terminal.write(lines[x]);
    }
  }, []);

  const socket = useSocketIO(socketIONamespace);
  const socketEventListener = React.useCallback(
    (data: { action: string; identity: string; output?: string }) => {
      if (!streamIdentity || data.identity !== streamIdentity) return;

      if (data["action"] == "sio_streamed_task_started") {
        xtermRef.current?.terminal.reset();
        return;
      }

      const shouldPrintLogs = [
        "sio_streamed_task_output",
        "sio_streamed_task_buffer",
      ].includes(data.action);

      if (shouldPrintLogs) {
        printLogs(data.output);
      }
    },
    [streamIdentity, printLogs]
  );
  React.useEffect(() => {
    if (streamIdentity) {
      socket?.emit("sio_streamed_task_data", {
        action: "sio_streamed_task_buffer_request",
        identity: streamIdentity,
      });
      socket?.on("sio_streamed_task_data", socketEventListener);
    }
    return () => {
      socket?.off("sio_streamed_task_data", socketEventListener);
    };
  }, [socket, xtermRef, socketEventListener, streamIdentity]);

  React.useEffect(() => {
    fitTerminal();
    window.addEventListener("resize", fitTerminal);
    return () => {
      window.removeEventListener("resize", fitTerminal);
    };
  }, [fitTerminal]);

  React.useEffect(() => {
    if (ignoreIncomingLogs) {
      xtermRef.current?.terminal.reset();
    }
  }, [ignoreIncomingLogs, xtermRef]);

  useEscapeToBlur();

  // Disallow the helper element to capture focus.
  React.useEffect(() => {
    const xtermHelperTextarea = document.querySelector(
      "textarea.xterm-helper-textarea"
    ) as HTMLElement;
    if (xtermHelperTextarea) xtermHelperTextarea.tabIndex = -1;
  }, []);

  const [isFocused, setIsFocused] = React.useState(false);

  const handleFocus = () => setIsFocused(true);
  const handleBlur = () => setIsFocused(false);

  return (
    <>
      {!hideDefaultStatus && (
        <ImageBuildStatus
          build={build}
          sx={{ margin: (theme) => theme.spacing(3, 0) }}
        />
      )}
      <Box
        sx={{
          overflow: "hidden",
          padding: (theme) => theme.spacing(1, 0, 0, 1),
          borderRadius: (theme) => theme.spacing(0.5),
          backgroundColor: (theme) => theme.palette.common.black,
          border: (theme) =>
            `2px solid ${
              isFocused ? theme.palette.primary.main : "transparent"
            } !important`,
        }}
        onFocus={handleFocus}
        onBlur={handleBlur}
      >
        <XTerm addons={[fitAddon]} ref={xtermRef} />
      </Box>
    </>
  );
};

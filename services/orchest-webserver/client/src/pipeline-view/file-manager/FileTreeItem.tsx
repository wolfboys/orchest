import { Code } from "@/components/common/Code";
import { OrchestFileIcon } from "@/components/common/icons/OrchestFileIcon";
import { useGlobalContext } from "@/contexts/GlobalContext";
import { useProjectsContext } from "@/contexts/ProjectsContext";
import { useSessionsContext } from "@/contexts/SessionsContext";
import TreeItem, { treeItemClasses, TreeItemProps } from "@mui/lab/TreeItem";
import { alpha } from "@mui/material";
import Box from "@mui/material/Box";
import { styled, SxProps, Theme } from "@mui/material/styles";
import React from "react";
import { cleanFilePath } from "./common";
import { useFileManagerContext } from "./FileManagerContext";
import { getIcon, SVGFileIcon } from "./SVGFileIcon";

const StyledTreeItemRoot = styled(TreeItem)(({ theme }) => ({
  [`& .${treeItemClasses.content}`]: {
    padding: theme.spacing(0, 0.5),
    [`.${treeItemClasses.label}`]: {
      paddingLeft: 0,
      ["div"]: {
        whiteSpace: "nowrap",
        textOverflow: "ellipsis",
        overflow: "hidden",
      },
    },
    "&.Mui-focused, &.Mui-selected, &.Mui-selected.Mui-focused": {
      backgroundColor: alpha(theme.palette.primary[700], 0.12),
      borderRadius: "4px",

      ["& + .Mui-selected"]: {
        backgroundColor: "red !important",
      },
    },
  },
}));

const DRAG_THRESHOLD = 3;

type FileTreeItemProps = TreeItemProps & {
  disableDragging?: boolean;
  fileName?: string;
  path?: string;
  labelText: string;
  sx: SxProps<Theme>;
};

export const FileTreeItem = ({
  disableDragging,
  fileName = "",
  path = "",
  labelText,
  onContextMenu,
  ...other
}: FileTreeItemProps) => {
  const { setIsDragging, setDragFile } = useFileManagerContext();
  const {
    state: { pipelines = [] },
  } = useProjectsContext();
  const { setConfirm } = useGlobalContext();
  const { getSession, stopSession } = useSessionsContext();

  const icon = !fileName ? undefined : fileName.endsWith(".orchest") ? (
    <OrchestFileIcon size={22} />
  ) : (
    getIcon(fileName)
  );

  const [pressed, setPressed] = React.useState(false);
  const [triggeredDragging, setTriggedDragging] = React.useState(false);
  const cumulativeDrag = React.useRef({ drag: 0 });

  const cancelMove = () => {
    setPressed(false);
    setTriggedDragging(false);
    cumulativeDrag.current.drag = 0;
  };

  return (
    <StyledTreeItemRoot
      onMouseDown={() => {
        if (!disableDragging) setPressed(true);
      }}
      onMouseMove={(e) => {
        if (!disableDragging && pressed && !triggeredDragging) {
          const normalizedDeltaX = e.movementX / window.devicePixelRatio;
          const normalizedDeltaY = e.movementY / window.devicePixelRatio;
          cumulativeDrag.current.drag +=
            Math.abs(normalizedDeltaX) + Math.abs(normalizedDeltaY);

          if (cumulativeDrag.current.drag > DRAG_THRESHOLD) {
            const filePathRelativeToProjectDir = cleanFilePath(path);
            const foundPipeline = pipelines.find(
              (pipeline) => pipeline.path === filePathRelativeToProjectDir
            );
            const session = getSession(foundPipeline?.uuid);

            if (foundPipeline?.uuid && session) {
              setConfirm(
                "Warning",
                <>
                  {`Before moving `}
                  <Code>{cleanFilePath(path, "Project files/")}</Code>
                  {`, you need to stop its session. Do you want to continue?`}
                </>,
                {
                  confirmLabel: "Stop session",
                  onConfirm: async (resolve) => {
                    stopSession(foundPipeline.uuid);
                    resolve(true);
                    return true;
                  },
                }
              );
              return;
            }
            setIsDragging(true);
            setDragFile({ labelText, path });
            setTriggedDragging(true);
          }
        }
      }}
      onMouseUp={cancelMove}
      onMouseLeave={cancelMove}
      onContextMenu={onContextMenu}
      label={
        <Box sx={{ fontSize: (theme) => theme.typography.body2.fontSize }}>
          {fileName && (
            <Box
              sx={{
                position: "absolute",
                overflow: "hidden",
                height: (theme) => theme.spacing(2.5),
                left: (theme) => theme.spacing(-2.75),
                top: 0,
              }}
            >
              <SVGFileIcon icon={icon} />
            </Box>
          )}
          {labelText}
        </Box>
      }
      {...other}
    />
  );
};

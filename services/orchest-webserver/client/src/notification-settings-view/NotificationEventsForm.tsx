import { DataTable, DataTableColumn } from "@/components/DataTable";
import { useAppInnerContext } from "@/contexts/AppInnerContext";
import Switch from "@mui/material/Switch";
import React from "react";
import { NotificationEventType, updateWebhook } from "./notification-webhooks";
import { useNotificationSettingsContext } from "./NotificationSettingsContext";

type NotificationEventTypeRow = { uuid: string } & Pick<
  NotificationEventType,
  "name"
>;

type NotificationEventTypeColumn = NotificationEventTypeRow & {
  toggle: React.ReactNode;
};

const eventExplanationMappings = {
  "project:one-off-job:failed": "One-off job fails",
  "project:one-off-job:pipeline-run:failed":
    "Any pipeline run from a one-off job fails",
  "project:cron-job:failed": "Cron job fails",
  "project:cron-job:run:failed": "Any recurring cron job fails",
  "project:cron-job:run:pipeline-run:failed":
    "Any pipeline run from a recurring cron job fails",
};

export const NotificationEventsForm = () => {
  const { webhooks } = useAppInnerContext();
  const {
    notificationEventTypes,
    enabledEventTypes,
    setEnabledEventTypes,
  } = useNotificationSettingsContext();

  const webhookUuids = React.useMemo(() => {
    return webhooks.map((hook) => hook.uuid);
  }, [webhooks]);

  const updateSubscriptions = React.useCallback(
    (eventTypes: string[]) => {
      return Promise.all(
        webhookUuids.map((uuid) =>
          updateWebhook(uuid, {
            subscriptions: eventTypes.map((event_type) => ({
              event_type,
            })),
          })
        )
      );
    },
    [webhookUuids]
  );

  const handleEvent = React.useCallback(
    (eventType: string, value: boolean) => {
      setEnabledEventTypes((current) => {
        const updatedEnabledEventTypes = value
          ? current
            ? [...new Set([...current, eventType])]
            : [eventType]
          : (current || []).filter(
              (existingEventType) => existingEventType !== eventType
            );

        updateSubscriptions(updatedEnabledEventTypes);

        return updatedEnabledEventTypes;
      });
    },
    [setEnabledEventTypes, updateSubscriptions]
  );

  const columns: DataTableColumn<
    NotificationEventTypeRow,
    NotificationEventTypeColumn
  >[] = [
    {
      id: "name",
      label: "Notify me when...",
      render: function renderEventTypeName(row) {
        return eventExplanationMappings[row.name];
      },
    },
    {
      id: "toggle",
      label: " ",
      render: function renderEventTypeToggle(row) {
        const isEnabled = enabledEventTypes.includes(row.name);
        return (
          <Switch
            size="small"
            disabled={webhookUuids.length === 0}
            inputProps={{
              "aria-label": `Switch ${isEnabled ? "off" : "on"} event: ${
                eventExplanationMappings[row.name]
              }`,
            }}
            sx={{ margin: (theme) => theme.spacing(0, 1) }}
            checked={webhookUuids.length === 0 ? false : isEnabled}
            onChange={(_, checked) => handleEvent(row.name, checked)}
          />
        );
      },
    },
  ];

  const eventTypeRows = notificationEventTypes.map((name) => ({
    uuid: name,
    name,
  }));

  return (
    <DataTable<NotificationEventTypeRow, NotificationEventTypeColumn>
      id="webhook-list"
      hideSearch
      disablePagination
      tableContainerElevation={0}
      columns={columns}
      rows={eventTypeRows}
      sx={{ padding: (theme) => theme.spacing(2, 0, 2, 6), width: "100%" }}
    />
  );
};

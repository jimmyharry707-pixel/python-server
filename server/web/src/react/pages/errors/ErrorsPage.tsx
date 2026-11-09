import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import {
  Card,
  ErrorBox,
  JsonDetail,
  Loading,
  Modal,
  PageHeader,
  Pager,
  Search,
  SimpleTable,
  when,
} from "../../ui";
import { UserIdentity, userPerson } from "../../components/Avatar";
const useFiltered = (rows: Row[], search: string) =>
  useMemo(
    () => rows.filter((r) => JSON.stringify(r).toLowerCase().includes(search.toLowerCase())),
    [rows, search],
  );
export function ErrorsPage() {
  const q = useQuery({ queryKey: ["errors"], queryFn: api.errors });
  const [find, setFind] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [page, setPage] = useState(1);
  const rows = useFiltered(q.data || [], find);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <>
      <PageHeader text="Review client error reports and diagnostic context." />
      <Card
        title="Error reports"
        actions={
          <Search
            value={find}
            onChange={(v) => {
              setFind(v);
              setPage(1);
            }}
          />
        }
      >
        <SimpleTable
          onRow={setDetail}
          rows={rows.slice((page - 1) * 25, page * 25)}
          columns={[
            { key: "created_at", label: "Time", render: (r) => when(r.created_at) },
            { key: "error_type", label: "Type", render: (r) => r.error_type || r.kind || "Error" },
            {
              key: "message",
              label: "Message",
              render: (r) => (
                <span className="text-truncate d-block" style={{ maxWidth: "32rem" }}>
                  {r.message || r.error_message}
                </span>
              ),
            },
            { key: "app_version", label: "Version" },
            {
              key: "user_email",
              label: "User",
              render: (row) =>
                row.user_id || row.user_email || row.email ? (
                  <UserIdentity person={userPerson(row)} />
                ) : (
                  "—"
                ),
            },
          ]}
        />
        <Pager page={page} total={rows.length} onChange={setPage} />
      </Card>
      {detail && (
        <Modal title="Error details" onClose={() => setDetail(null)}>
          <JsonDetail data={detail} />
        </Modal>
      )}
    </>
  );
}

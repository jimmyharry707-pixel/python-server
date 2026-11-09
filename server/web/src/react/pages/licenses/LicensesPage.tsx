import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { ErrorBox, Loading, PageHeader } from "../../ui";
import { EventsTab } from "./EventsTab";
import { LicensesTab } from "./LicensesTab";
import { MachinesTab } from "./MachinesTab";

type Tab = "licenses" | "machines" | "events";

const PRODUCT_KEY = "baymax_licenses_product";

function savedProduct() {
  try {
    return localStorage.getItem(PRODUCT_KEY) || "";
  } catch {
    return "";
  }
}

export function LicensesPage() {
  const products = useQuery({ queryKey: ["license-products"], queryFn: api.licenseProducts });
  const [tab, setTab] = useState<Tab>("licenses");
  const [product, setProductState] = useState(savedProduct);
  const [issuing, setIssuing] = useState(false);
  const setProduct = (value: string) => {
    setProductState(value);
    try {
      localStorage.setItem(PRODUCT_KEY, value);
    } catch {}
  };

  if (products.isLoading) return <Loading />;
  if (products.error) return <ErrorBox error={products.error} />;
  const list: Row[] = products.data || [];
  const selected = list.find((item) => item.id === product);
  const sum = (field: string) =>
    (selected ? [selected] : list).reduce((total, item) => total + (Number(item[field]) || 0), 0);

  const tabs: { key: Tab; label: string; icon: string; count?: number }[] = [
    { key: "licenses", label: "Keys", icon: "ti-key", count: sum("licenses") },
    {
      key: "machines",
      label: "Machines",
      icon: "ti-device-desktop",
      count: sum("active_machines"),
    },
    { key: "events", label: "Events", icon: "ti-history" },
  ];
  const filters = [
    {
      id: "",
      name: "All apps",
      count: list.reduce((t, item) => t + (Number(item.licenses) || 0), 0),
    },
    ...list.map((item) => ({ id: item.id, name: item.name, count: Number(item.licenses) || 0 })),
  ];

  return (
    <div className="stage-lic-page">
      <PageHeader
        text="License keys for CareerCraft, Sidekick and SnowLink, and every machine that uses them."
        action={
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setTab("licenses");
              setIssuing(true);
            }}
          >
            <i className="ti ti-plus me-1" />
            Issue key
          </button>
        }
      />
      <div className="stage-users-toolbar stage-lic-toolbar">
        <div className="btn-group stage-users-tabs" role="tablist" aria-label="License views">
          {tabs.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={tab === item.key}
              className={`btn ${tab === item.key ? "btn-primary" : "btn-outline-secondary"}`}
              onClick={() => setTab(item.key)}
            >
              <i className={`ti ${item.icon} me-1`} aria-hidden="true" />
              {item.label}
              {item.count !== undefined && (
                <span
                  className={`badge ms-2 ${tab === item.key ? "bg-white text-primary" : "bg-secondary-lt"}`}
                >
                  {item.count}
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="btn-group btn-group-sm stage-lic-products" role="group" aria-label="App">
          {filters.map((item) => (
            <button
              key={item.id || "all"}
              type="button"
              aria-pressed={product === item.id}
              className={`btn ${product === item.id ? "btn-secondary" : "btn-outline-secondary"}`}
              onClick={() => setProduct(item.id)}
            >
              {item.name}
              <span className="ms-1 opacity-75">{item.count}</span>
            </button>
          ))}
        </div>
      </div>
      {tab === "licenses" && (
        <LicensesTab product={product} products={list} issuing={issuing} setIssuing={setIssuing} />
      )}
      {tab === "machines" && <MachinesTab product={product} />}
      {tab === "events" && <EventsTab product={product} />}
    </div>
  );
}

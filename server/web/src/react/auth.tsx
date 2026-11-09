import { createContext, useContext } from "react";
import type { CurrentUser } from "./api";
export const UserContext = createContext<CurrentUser | null>(null);
export const useMe = () => {
  const v = useContext(UserContext);
  if (!v) throw new Error("User context unavailable");
  return v;
};

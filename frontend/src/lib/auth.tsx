import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, login as apiLogin, setUnauthorizedHandler, tokenStore } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  status: "loading" | "authenticated" | "anonymous";
  /** Why the user was signed out (shown on the login page), e.g. "Session expired". */
  signedOutReason: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: (reason?: string) => void;
  isAdmin: boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>(tokenStore.get() ? "loading" : "anonymous");
  const [signedOutReason, setReason] = useState<string | null>(null);

  const signOut = useCallback(
    (reason?: string) => {
      tokenStore.clear();
      setUser(null);
      setStatus("anonymous");
      setReason(reason ?? null);
      qc.clear();
    },
    [qc],
  );

  useEffect(() => {
    setUnauthorizedHandler((msg) =>
      signOut(msg === "Session expired" ? "Your session expired. Please sign in again." : msg),
    );
  }, [signOut]);

  useEffect(() => {
    if (!tokenStore.get()) return;
    api<User>("/api/auth/me")
      .then((u) => {
        setUser(u);
        setStatus("authenticated");
      })
      .catch((e: ApiError) => {
        // Network errors keep the token so a server restart doesn't log everyone out.
        if (e.isNetwork) setStatus("anonymous");
        else signOut(e.isAuth ? "Your session expired. Please sign in again." : undefined);
      });
  }, [signOut]);

  const signIn = useCallback(async (email: string, password: string) => {
    const { access_token } = await apiLogin(email, password);
    tokenStore.set(access_token);
    const me = await api<User>("/api/auth/me");
    setUser(me);
    setReason(null);
    setStatus("authenticated");
  }, []);

  const value = useMemo(
    () => ({ user, status, signedOutReason, signIn, signOut, isAdmin: user?.role === "admin" }),
    [user, status, signedOutReason, signIn, signOut],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

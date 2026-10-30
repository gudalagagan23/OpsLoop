"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getAuthJson, getToken, setToken, type Me } from "@/lib/api";

type AuthState = {
  me: Me | null;
  token: string;
  signIn: (token: string) => Promise<void>;
  signOut: () => void;
};

const AuthContext = createContext<AuthState>({
  me: null,
  token: "",
  signIn: async () => {},
  signOut: () => {},
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTok] = useState("");
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const saved = getToken();
    if (!saved) return;
    getAuthJson<Me>("/me", saved)
      .then((m) => {
        setTok(saved);
        setMe(m);
      })
      .catch(() => setToken(""));
  }, []);

  const signIn = useCallback(async (t: string) => {
    const m = await getAuthJson<Me>("/me", t.trim());
    setToken(t.trim());
    setTok(t.trim());
    setMe(m);
  }, []);

  const signOut = useCallback(() => {
    setToken("");
    setTok("");
    setMe(null);
  }, []);

  return <AuthContext.Provider value={{ me, token, signIn, signOut }}>{children}</AuthContext.Provider>;
}

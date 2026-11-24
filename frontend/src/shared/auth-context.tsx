import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api, setCsrfToken } from "./api";
import type { AuthUser } from "./types";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const clear = useCallback(() => {
    setUser(null);
    setCsrfToken("");
    queryClient.clear();
  }, [queryClient]);

  const refresh = useCallback(async () => {
    try {
      const result = await api.me();
      if (result.user) {
        setUser(result.user);
        setCsrfToken(result.csrf_token);
      } else {
        clear();
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) clear();
    }
  }, [clear]);

  useEffect(() => {
    let active = true;
    api
      .me()
      .then((result) => {
        if (!active) return;
        if (result.user) {
          setUser(result.user);
          setCsrfToken(result.csrf_token);
        } else {
          clear();
        }
      })
      .catch(() => {
        if (active) clear();
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    const onUnauthorized = () => {
      if (active) clear();
    };
    window.addEventListener("codata:unauthorized", onUnauthorized);
    return () => {
      active = false;
      window.removeEventListener("codata:unauthorized", onUnauthorized);
    };
  }, [clear]);

  const login = useCallback(
    async (username: string, password: string) => {
      const result = await api.login(username, password);
      if (!result.user) throw new Error("登录失败");
      queryClient.clear();
      setUser(result.user);
      setCsrfToken(result.csrf_token);
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      clear();
    }
  }, [clear]);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      await api.changePassword(currentPassword, newPassword);
      await refresh();
    },
    [refresh],
  );

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, login, logout, changePassword, refresh }),
    [user, loading, login, logout, changePassword, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}

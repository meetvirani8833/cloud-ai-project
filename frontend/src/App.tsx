import { Component, lazy, Suspense, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { Button, Loading } from "./components/ui/primitives";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";
import Overview from "./pages/Overview";

// Secondary pages load on demand to keep the first paint small.
const Anomalies = lazy(() => import("./pages/Anomalies"));
const Evaluation = lazy(() => import("./pages/Evaluation"));
const Recommendations = lazy(() => import("./pages/Recommendations"));
const Reports = lazy(() => import("./pages/Reports"));
const Settings = lazy(() => import("./pages/Settings"));
const ServicesPage = lazy(() => import("./pages/Services").then((m) => ({ default: m.ServicesPage })));
const ServiceDetail = lazy(() => import("./pages/Services").then((m) => ({ default: m.ServiceDetail })));
const TenantsPage = lazy(() => import("./pages/Tenants").then((m) => ({ default: m.TenantsPage })));
const TenantDetail = lazy(() => import("./pages/Tenants").then((m) => ({ default: m.TenantDetail })));

const pageFallback = <Loading className="h-64" />;

function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const loc = useLocation();
  if (status === "loading") return <Loading className="h-full" label="Signing you in" />;
  if (status === "anonymous") return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return <>{children}</>;
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  return isAdmin ? <>{children}</> : <NotAllowed />;
}

function NotAllowed() {
  return (
    <div className="grid h-full place-items-center p-8 text-center">
      <div><p className="text-sm font-semibold text-brand-600">403</p><h1 className="mt-1 text-xl font-bold">Admins only</h1>
        <p className="mt-2 text-sm text-muted">Your account has view access. Ask an admin if you need to change settings.</p>
        <Link to="/"><Button className="mt-5">Back to overview</Button></Link></div>
    </div>
  );
}

function NotFound() {
  return (
    <div className="grid h-full place-items-center p-8 text-center">
      <div><p className="text-sm font-semibold text-brand-600">404</p><h1 className="mt-1 text-xl font-bold">Page not found</h1>
        <p className="mt-2 text-sm text-muted">The page you're looking for doesn't exist or was moved.</p>
        <Link to="/"><Button className="mt-5">Back to overview</Button></Link></div>
    </div>
  );
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div><h1 className="text-xl font-bold">Something went wrong</h1>
          <p className="mt-2 max-w-md text-sm text-muted">{this.state.error.message}</p>
          <Button className="mt-5" onClick={() => window.location.reload()}>Reload page</Button></div>
      </div>
    );
  }
}

export default function App() {
  const { user } = useAuth();
  return (
    <ErrorBoundary>
      <Suspense fallback={pageFallback}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<Overview />} />
          <Route path="recommendations" element={<Recommendations />} />
          <Route path="tenants" element={user?.tenant_id ? <Navigate to={`/tenants/${user.tenant_id}`} replace /> : <TenantsPage />} />
          <Route path="tenants/:id" element={<TenantDetail />} />
          <Route path="services" element={<ServicesPage />} />
          <Route path="services/:id" element={<ServiceDetail />} />
          <Route path="anomalies" element={<Anomalies />} />
          <Route path="evaluation" element={<Evaluation />} />
          <Route path="reports" element={<Reports />} />
          <Route path="settings" element={<AdminOnly><Settings /></AdminOnly>} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}

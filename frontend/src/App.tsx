import { BrowserRouter, Routes, Route } from "react-router";
import { WebSocketProvider } from "./ws/WebSocketContext";
import { Sidebar } from "./components/Sidebar";
import { AgentPage } from "./pages/AgentPage";
import { PoliciesPage } from "./pages/PoliciesPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AuditLogsPage } from "./pages/AuditLogsPage";

export default function App() {
  return (
    <WebSocketProvider>
      <BrowserRouter>
        <div className="flex min-h-screen bg-zinc-950">
          <Sidebar />
          <main className="flex-1 p-8">
            <Routes>
              <Route path="/" element={<AgentPage />} />
              <Route path="/policies" element={<PoliciesPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/audit" element={<AuditLogsPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </WebSocketProvider>
  );
}

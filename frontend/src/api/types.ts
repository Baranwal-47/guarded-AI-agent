export type Action = "ALLOW" | "DENY" | "REQUIRE_APPROVAL";

export type WsEvent =
  | { type: "tool_requested"; tool_name: string; arguments: Record<string, unknown>; conversation_id: string }
  | {
      type: "policy_decided";
      tool_name: string;
      action: Action;
      reason: string;
      matched_rule_ids: string[];
      conversation_id: string;
    }
  | {
      type: "approval_required";
      request_id: string;
      tool_name: string;
      arguments: Record<string, unknown>;
      reason: string;
      conversation_id: string;
    }
  | { type: "approval_granted"; request_id: string; tool_name: string; conversation_id: string }
  | { type: "approval_rejected"; request_id: string; tool_name: string; conversation_id: string }
  | { type: "execution_started"; tool_name: string; conversation_id: string }
  | { type: "execution_completed"; tool_name: string; result_ok: true; conversation_id: string }
  | { type: "execution_failed"; tool_name: string; result_ok: false; result_error: string; conversation_id: string };

export interface Tool {
  name: string;
  description?: string;
  [key: string]: unknown;
}

export interface PolicyRule {
  id: string;
  policy_id: string | null;
  rule_type: string;
  tool_name: string;
  condition: Record<string, unknown>;
  action: Action;
  enabled: boolean;
}

export interface ApprovalRequest {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  status: string;
  decided_by: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface ToolExecution {
  id: string;
  conversation_id: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  decision_action: Action;
  decision_reason: string;
  matched_rule_ids: string[];
  result_ok: boolean | null;
  result_error: string | null;
  flagged_prompt_injection: boolean;
  created_at: string;
}

export interface AuditLog {
  id: string;
  event: string;
  detail: Record<string, unknown>;
  flags: string | null;
  created_at: string;
}

export interface ChatMessage {
  role: string;
  content: string;
  created_at: string;
}

export interface ChatState {
  pending_approvals: ApprovalRequest[];
  recent_messages: ChatMessage[];
}

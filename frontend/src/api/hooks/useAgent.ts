import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  agentChatAgentChatPost,
  agentResumeAgentResumePost,
  agentThreadStateAgentThreadThreadIdStateGet,
  emailSourceStatusEndpointAgentEmailSourceStatusGet,
} from '@/api/generated/agent/agent';
import {
  AgentChatIn,
  AgentResumeIn,
  AgentTurnOut,
  AgentThreadStateOut,
  EmailSourceStatusOut,
} from '@/api/generated/model';
import { QUERY_KEYS, invalidateLedgerData } from '@/api/invalidation';

export function useAgentThreadState(threadId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.agentThread(threadId),
    queryFn: (): Promise<AgentThreadStateOut> => {
      return agentThreadStateAgentThreadThreadIdStateGet(threadId);
    },
    enabled: Boolean(threadId && threadId.trim().length > 0),
  });
}

export function useEmailSourceStatus() {
  return useQuery({
    queryKey: ['agent', 'email-source-status'],
    queryFn: (): Promise<EmailSourceStatusOut> => {
      return emailSourceStatusEndpointAgentEmailSourceStatusGet();
    },
  });
}

export function useAgentChat() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: AgentChatIn): Promise<AgentTurnOut> => {
      return agentChatAgentChatPost(payload);
    },
    onSuccess: (turnOut) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentThread(turnOut.thread_id) });
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.artifacts });
    },
  });
}

export function useAgentResume() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: AgentResumeIn): Promise<AgentTurnOut> => {
      return agentResumeAgentResumePost(payload);
    },
    onSuccess: async (turnOut, variables) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agentThread(turnOut.thread_id) });
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.artifacts });

      // If approved, changes were applied to ledger -> invalidate ledger data
      if (variables.decision === 'approve') {
        await invalidateLedgerData(queryClient);
      }
    },
  });
}

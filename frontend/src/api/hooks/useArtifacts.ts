import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listArtifactsArtifactsGet,
  getArtifactArtifactsArtifactIdGet,
  getArtifactRowsArtifactsArtifactIdRowsGet,
  deriveArtifactArtifactsArtifactIdDerivePost,
  deleteArtifactArtifactsArtifactIdDelete,
} from '@/api/generated/artifacts/artifacts';
import {
  ArtifactSummary,
  ArtifactOut,
  ArtifactDeriveIn,
} from '@/api/generated/model';
import { QUERY_KEYS } from '@/api/invalidation';

export function useArtifactsList(threadId?: string, kind?: string, status: string = 'open') {
  return useQuery({
    queryKey: ['artifacts', 'list', { threadId, kind, status }],
    queryFn: (): Promise<ArtifactSummary[]> => {
      return listArtifactsArtifactsGet({
        thread_id: threadId,
        kind,
        status,
      });
    },
  });
}

/**
 * Directly fetch single artifact by ID (supports instant Agent -> Canvas sync).
 */
export function useArtifact(artifactId?: number) {
  return useQuery({
    queryKey: artifactId != null ? QUERY_KEYS.artifactDetail(artifactId) : ['artifacts', 'none'],
    queryFn: (): Promise<ArtifactOut> => {
      return getArtifactArtifactsArtifactIdGet(artifactId!);
    },
    enabled: artifactId != null && !isNaN(artifactId),
  });
}

/**
 * Fetch materialized rows for an artifact without LLM reasoning tokens.
 */
export function useArtifactRows(
  artifactId?: number,
  params: {
    limit?: number;
    offset?: number;
    sort?: string;
    force_refresh?: boolean;
  } = {}
) {
  return useQuery({
    queryKey: artifactId != null ? ['artifacts', artifactId, 'rows', params] : ['artifacts', 'none', 'rows'],
    queryFn: (): Promise<any> => {
      return getArtifactRowsArtifactsArtifactIdRowsGet(artifactId!, params);
    },
    enabled: artifactId != null && !isNaN(artifactId),
  });
}

export function useDeriveArtifact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      artifactId,
      payload,
    }: {
      artifactId: number;
      payload: ArtifactDeriveIn;
    }): Promise<ArtifactOut> => {
      return deriveArtifactArtifactsArtifactIdDerivePost(artifactId, payload);
    },
    onSuccess: (newArtifact) => {
      queryClient.setQueryData(QUERY_KEYS.artifactDetail(newArtifact.id), newArtifact);
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.artifacts });
    },
  });
}

/**
 * Dismiss an artifact (sets status=expired, returns 204).
 */
export function useDismissArtifact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (artifactId: number): Promise<void> => {
      return deleteArtifactArtifactsArtifactIdDelete(artifactId);
    },
    onSuccess: (_, artifactId) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.artifacts });
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.artifactDetail(artifactId) });
    },
  });
}

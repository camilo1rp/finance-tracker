import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AgentTransactionTable } from './AgentTransactionTable';

// Mock next/navigation
const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
  }),
}));

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe('AgentTransactionTable Dynamic Features', () => {
  const mockArtifact = {
    id: 101,
    title: 'Top Dining Expenses',
    spec: {
      tool: 'list_transactions',
      kwargs: { category: 'Dining', date_from: '2026-01-01', date_to: '2026-01-31' },
    },
    digest: {
      match_count: 3,
      sample_rows: [
        { id: 1, transaction_date: '2026-01-05', description: 'Bistro Lunch', effective_category: 'Dining', amount: 32.5 },
        { id: 2, transaction_date: '2026-01-12', description: 'Sushi Dinner', effective_category: 'Dining', amount: 95.0 },
        { id: 3, transaction_date: '2026-01-20', description: 'Coffee Roasters', effective_category: 'Dining', amount: 6.25 },
      ],
    },
  };

  it('renders artifact title, dynamic count badge, and action links', () => {
    renderWithClient(<AgentTransactionTable artifact={mockArtifact} />);

    expect(screen.getByText('Top Dining Expenses')).toBeDefined();
    expect(screen.getByText(/3 matching transactions/)).toBeDefined();
    expect(screen.getByText('Full Artifact #101')).toBeDefined();
    expect(screen.getByText('Ledger View')).toBeDefined();
  });

  it('allows in-table search filtering across transactions', () => {
    renderWithClient(<AgentTransactionTable artifact={mockArtifact} />);

    const searchInput = screen.getByPlaceholderText('Filter transactions in this table...');
    expect(searchInput).toBeDefined();

    fireEvent.change(searchInput, { target: { value: 'Sushi' } });

    expect(screen.getByText('Sushi Dinner')).toBeDefined();
    expect(screen.queryByText('Bistro Lunch')).toBeNull();
  });

  it('navigates to transaction detail when row is clicked', () => {
    mockPush.mockClear();
    renderWithClient(<AgentTransactionTable artifact={mockArtifact} />);

    fireEvent.click(screen.getByText('Bistro Lunch'));
    expect(mockPush).toHaveBeenCalledWith('/transactions/1');
  });

  it('navigates to full artifact page when Full Artifact link is clicked', () => {
    mockPush.mockClear();
    renderWithClient(<AgentTransactionTable artifact={mockArtifact} />);

    fireEvent.click(screen.getByText('Full Artifact #101'));
    expect(mockPush).toHaveBeenCalledWith('/artifacts/101');
  });
});

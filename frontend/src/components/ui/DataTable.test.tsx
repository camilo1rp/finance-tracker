import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { DataTable, Column } from './DataTable';

interface TestRow {
  id: number;
  name: string;
  amount: number;
}

const mockData: TestRow[] = [
  { id: 1, name: 'Coffee', amount: 4.5 },
  { id: 2, name: 'Groceries', amount: 85.0 },
  { id: 3, name: 'Apple Store', amount: 1200.0 },
  { id: 4, name: 'Bakery', amount: 12.0 },
  { id: 5, name: 'Gas', amount: 45.0 },
];

const columns: Column<TestRow>[] = [
  { header: 'ID', accessorKey: 'id', sortable: true },
  { header: 'Name', accessorKey: 'name', sortable: true },
  { header: 'Amount', accessorKey: 'amount', sortable: true },
];

describe('DataTable dynamic capabilities', () => {
  it('renders rows and headers correctly', () => {
    render(
      <DataTable
        data={mockData}
        columns={columns}
        keyExtractor={(item) => item.id}
      />
    );

    expect(screen.getByText('Coffee')).toBeDefined();
    expect(screen.getByText('Groceries')).toBeDefined();
    expect(screen.getByText('Apple Store')).toBeDefined();
  });

  it('filters rows when searchable is enabled and user types in search box', () => {
    render(
      <DataTable
        data={mockData}
        columns={columns}
        keyExtractor={(item) => item.id}
        searchable={true}
        searchPlaceholder="Search test..."
      />
    );

    const input = screen.getByPlaceholderText('Search test...');
    fireEvent.change(input, { target: { value: 'app' } });

    expect(screen.getByText('Apple Store')).toBeDefined();
    expect(screen.queryByText('Groceries')).toBeNull();
    expect(screen.queryByText('Coffee')).toBeNull();
  });

  it('sorts rows when clicking sortable column headers', () => {
    render(
      <DataTable
        data={mockData}
        columns={columns}
        keyExtractor={(item) => item.id}
      />
    );

    const nameHeader = screen.getByText('Name');

    // First click: ascending (Apple Store, Bakery, Coffee, Gas, Groceries)
    fireEvent.click(nameHeader);
    const cellsAsc = screen.getAllByRole('cell');
    expect(cellsAsc[1].textContent).toBe('Apple Store');

    // Second click: descending (Groceries, Gas, Coffee, Bakery, Apple Store)
    fireEvent.click(nameHeader);
    const cellsDesc = screen.getAllByRole('cell');
    expect(cellsDesc[1].textContent).toBe('Groceries');
  });

  it('paginates data with pageSize and navigation buttons', () => {
    render(
      <DataTable
        data={mockData}
        columns={columns}
        keyExtractor={(item) => item.id}
        pagination={true}
        pageSize={2}
      />
    );

    // Page 1: shows Coffee and Groceries (items 1 & 2)
    expect(screen.getByText('Coffee')).toBeDefined();
    expect(screen.getByText('Groceries')).toBeDefined();
    expect(screen.queryByText('Apple Store')).toBeNull();
    expect(
      screen.getByText((_, element) => element?.tagName.toLowerCase() === 'span' && element?.textContent === 'Page 1 of 3')
    ).toBeDefined();

    // Next page
    const nextBtn = screen.getByTitle('Next page');
    fireEvent.click(nextBtn);

    // Page 2: shows Apple Store and Bakery (items 3 & 4)
    expect(screen.getByText('Apple Store')).toBeDefined();
    expect(screen.getByText('Bakery')).toBeDefined();
    expect(screen.queryByText('Coffee')).toBeNull();
    expect(
      screen.getByText((_, element) => element?.tagName.toLowerCase() === 'span' && element?.textContent === 'Page 2 of 3')
    ).toBeDefined();
  });

  it('calls onRowClick when a row is clicked', () => {
    const handleRowClick = vi.fn();
    render(
      <DataTable
        data={mockData}
        columns={columns}
        keyExtractor={(item) => item.id}
        onRowClick={handleRowClick}
      />
    );

    fireEvent.click(screen.getByText('Coffee'));
    expect(handleRowClick).toHaveBeenCalledWith(mockData[0]);
  });
});

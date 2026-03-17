# Prompt

Create a functional React component for a sortable and filterable data table using React Hooks and Tailwind CSS. It should handle thousands of rows efficiently using virtualization.

# Response

Here is a complete, drop-in solution. It uses **React Hooks** for logic and **`react-window`** for virtualization (rendering only the rows currently visible on screen), ensuring smooth performance even with 10,000+ rows.

### Prerequisites

You need to install `react-window` for the virtualization to work:

```bash
npm install react-window
# or
yarn add react-window
```

### The Component Code

```jsx
import React, { useState, useMemo, useCallback } from 'react';
import { FixedSizeList as List } from 'react-window';

// --- Mock Data Generator (Simulating a heavy dataset) ---
const generateData = (count) => {
  const data = [];
  const firstNames = ['James', 'Mary', 'Robert', 'Patricia', 'John', 'Jennifer', 'Michael', 'Linda', 'William', 'Elizabeth'];
  const lastNames = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez'];
  const departments = ['Engineering', 'Sales', 'Marketing', 'HR', 'Finance', 'Operations'];
  
  for (let i = 0; i < count; i++) {
    data.push({
      id: i + 1,
      firstName: firstNames[Math.floor(Math.random() * firstNames.length)],
      lastName: lastNames[Math.floor(Math.random() * lastNames.length)],
      email: `user${i}@example.com`,
      role: 'Developer',
      department: departments[Math.floor(Math.random() * departments.length)],
      salary: Math.floor(Math.random() * 100000) + 50000,
    });
  }
  return data;
};

// --- Row Component (Memoized for performance) ---
const Row = React.memo(({ index, style, data }) => {
  const row = data[index];
  return (
    <div style={style} className="flex items-center border-b border-gray-200 hover:bg-gray-50 px-4 py-3">
      <div className="flex-1 font-medium text-gray-700">{row.id}</div>
      <div className="flex-1 text-gray-700">{row.firstName} {row.lastName}</div>
      <div className="flex-1 text-gray-600 text-sm">{row.email}</div>
      <div className="flex-1 text-gray-600 text-sm">{row.role}</div>
      <div className="flex-1 text-gray-600 text-sm">{row.department}</div>
      <div className="flex-1 text-gray-700 font-semibold">${row.salary.toLocaleString()}</div>
    </div>
  );
});

// --- Main Table Component ---
export const VirtualDataTable = () => {
  // 1. Initialize State
  const [data] = useState(() => generateData(10000)); // 10k rows
  const [searchQuery, setSearchQuery] = useState('');
  const [sortConfig, setSortConfig] = useState({ key: 'id', direction: 'ascending' });

  // 2. Handle Sorting
  const handleSort = (key) => {
    let direction = 'ascending';
    if (sortConfig.key === key && sortConfig.direction === 'ascending') {
      direction = 'descending';
    }
    setSortConfig({ key, direction });
  };

  // 3. Filter and Sort Logic (Memoized to prevent recalculation on every render)
  const processedData = useMemo(() => {
    let filtered = [...data];

    // Filter
    if (searchQuery) {
      const lowerQuery = searchQuery.toLowerCase();
      filtered = filtered.filter((item) =>
        Object.values(item).some((val) =>
          String(val).toLowerCase().includes(lowerQuery)
        )
      );
    }

    // Sort
    if (sortConfig.key) {
      filtered.sort((a, b) => {
        if (a[sortConfig.key] < b[sortConfig.key]) {
          return sortConfig.direction === 'ascending' ? -1 : 1;
        }
        if (a[sortConfig.key] > b[sortConfig.key]) {
          return sortConfig.direction === 'ascending' ? 1 : -1;
        }
        return 0;
      });
    }

    return filtered;
  }, [data, searchQuery, sortConfig]);

  // 4. Render Helper for Table Header
  const renderHeader = (label, key) => (
    <button
      key={key}
      onClick={() => handleSort(key)}
      className={`flex-1 px-4 py-3 text-left text-sm font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-2 focus:outline-none transition-colors ${
        sortConfig.key === key ? 'text-blue-600' : 'hover:text-gray-700'
      }`}
    >
      {label}
      {sortConfig.key === key && (
        <span className="text-xs">
          {sortConfig.direction === 'ascending' ? '↑' : '↓'}
        </span>
      )}
    </button>
  );

  return (
    <div className="w-full max-w-7xl mx-auto p-6 bg-white shadow-xl rounded-lg border border-gray-200 font-sans">
      
      {/* Header / Controls */}
      <div className="mb-6 flex justify-between items-center">
        <h2 className="text-2xl font-bold text-gray-800">Employee Directory</h2>
        <input
          type="text"
          placeholder="Search data..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none w-64"
        />
      </div>

      {/* Table Container */}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <div className="overflow-y-auto h-[600px]">
          <List
            height={600}
            width="100%"
            itemCount={processedData.length}
            itemSize={50} // Height of each row in pixels
            className="flex flex-col"
          >
            {({ index, style }) => (
              <div style={style} className="flex flex-col">
                {/* Render Header only for the first row (index 0) */}
                {index === 0 && (
                  <div className="flex border-b-2 border-gray-300 bg-gray-50 sticky top-0 z-10">
                    {renderHeader('ID', 'id')}
                    {renderHeader('Name', 'firstName')}
                    {renderHeader('Email', 'email')}
                    {renderHeader('Role', 'role')}
                    {renderHeader('Dept', 'department')}
                    {renderHeader('Salary', 'salary')}
                  </div>
                )}
                {/* Render Data Row */}
                {index !== 0 && <Row index={index - 1} style={style} data={processedData} />}
              </div>
            )}
          </List>
        </div>
        
        {/* Empty State */}
        {processedData.length === 0 && (
          <div className="p-10 text-center text-gray-500">
            No results found for "{searchQuery}"
          </div>
        )}
      </div>
      
      <div className="mt-2 text-sm text-gray-500">
        Showing {processedData.length} of {data.length} records
      </div>
    </div>
  );
};

export default VirtualDataTable;
```

### Key Features Explained

1.  **Virtualization (`react-window`)**:
    *   The `List` component from `react-window` is the core engine. It calculates which rows fit into the 600px height container.
    *   It renders only those specific rows (usually 10-15) plus a small buffer.
    *   This reduces the DOM node count from ~10,000 to ~15, making the browser handle the scroll at a buttery smooth 60fps.

2.  **Memoization (`useMemo` & `React.memo`)**:
    *   **`processedData`**: Sorting and filtering 10,000 items is computationally expensive. We wrap this in `useMemo` so the calculation only runs when `searchQuery` or `sortConfig` changes, not on every single render cycle.
    *   **`Row`**: We wrap the Row component in `React.memo`. This ensures that when you scroll, React doesn't re-render the DOM nodes for rows that are already visible or haven't changed.

3.  **Sticky Header**:
    *   The header is rendered inside the `List` component logic at `index === 0`.
    *   It is styled with `sticky top-0 z-10`. This ensures the column names stay visible while you scroll through the data, which is a crucial UX requirement for data tables.

4.  **Tailwind CSS**:
    *   Used for layout (flexbox), spacing, typography, and interaction states (hover, focus rings).
    *   The container is responsive (`max-w-7xl mx-auto`) and has a clean, modern shadow aesthetic.

### How to use

Simply import the component into your main App file:

```jsx
import VirtualDataTable from './VirtualDataTable';

function App() {
  return (
    <div className="min-h-screen bg-gray-100 py-10">
      <VirtualDataTable />
    </div>
  );
}
```
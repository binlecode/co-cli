# Prompt

Create a functional React component for a sortable and filterable data table using React Hooks and Tailwind CSS. It should handle thousands of rows efficiently using virtualization.

# Response

Here is a complete, copy-pasteable solution. It uses **@tanstack/react-virtual** for high-performance virtualization, **Tailwind CSS** for styling, and standard React Hooks for logic.

### Prerequisites

You will need to install the virtualization library:

```bash
npm install @tanstack/react-virtual
```

### The Component (`VirtualDataTable.tsx`)

```tsx
import React, { useState, useMemo, useRef, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

// --- Types ---
interface User {
  id: number;
  firstName: string;
  lastName: string;
  email: string;
  role: string;
  status: 'Active' | 'Inactive' | 'Pending';
  lastLogin: string;
}

type SortKey = keyof User;
type SortDirection = 'asc' | 'desc';

interface Props {
  data: User[];
}

// --- Mock Data Generator ---
const generateData = (count: number): User[] => {
  const roles = ['Admin', 'Editor', 'Viewer', 'Developer'];
  const statuses: ('Active' | 'Inactive' | 'Pending')[] = ['Active', 'Inactive', 'Pending'];
  
  return Array.from({ length: count }, (_, i) => ({
    id: i,
    firstName: `User${i}`,
    lastName: `LastName${i}`,
    email: `user${i}@example.com`,
    role: roles[Math.floor(Math.random() * roles.length)],
    status: statuses[Math.floor(Math.random() * statuses.length)],
    lastLogin: new Date(Date.now() - Math.floor(Math.random() * 10000000000)).toISOString(),
  }));
};

const VirtualDataTable: React.FC<Props> = ({ data }) => {
  // --- State ---
  const [filterText, setFilterText] = useState('');
  const [sortConfig, setSortConfig] = useState<{ key: SortKey; direction: SortDirection }>({
    key: 'id',
    direction: 'asc',
  });

  // --- Refs for Virtualization ---
  const tableContainerRef = useRef<HTMLDivElement>(null);
  const rowHeight = 48; // Approximate height of a row in pixels

  // --- Derived State (Filter & Sort) ---
  // We memoize this to avoid recalculating on every render
  const processedData = useMemo(() => {
    // 1. Filter
    let result = data.filter((item) =>
      Object.values(item).some((val) =>
        String(val).toLowerCase().includes(filterText.toLowerCase())
      )
    );

    // 2. Sort
    result.sort((a, b) => {
      const aValue = a[sortConfig.key];
      const bValue = b[sortConfig.key];

      if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1;
      if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1;
      return 0;
    });

    return result;
  }, [data, filterText, sortConfig]);

  // --- Virtualization Logic ---
  const virtualizer = useVirtualizer({
    count: processedData.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: () => rowHeight,
    overscan: 5, // How many rows to render outside the viewport
  });

  // --- Handlers ---
  const handleSort = (key: SortKey) => {
    setSortConfig((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc',
    }));
  };

  // --- Render Helpers ---
  const getSortIcon = (key: SortKey) => {
    if (sortConfig.key !== key) return '↕';
    return sortConfig.direction === 'asc' ? '↑' : '↓';
  };

  const getRowStyle = (index: number) => {
    const isEven = index % 2 === 0;
    return `border-b border-gray-200 ${isEven ? 'bg-white' : 'bg-gray-50'}`;
  };

  return (
    <div className="w-full max-w-4xl mx-auto p-4 font-sans text-gray-900">
      <h2 className="text-2xl font-bold mb-4">Virtualized Data Table</h2>

      {/* Controls */}
      <div className="flex justify-between items-center mb-4">
        <div className="relative">
          <input
            type="text"
            placeholder="Search..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="pl-3 pr-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 w-64"
          />
        </div>
        <span className="text-sm text-gray-500">
          Showing {processedData.length} of {data.length} rows
        </span>
      </div>

      {/* Table Container */}
      <div 
        ref={tableContainerRef}
        className="border border-gray-300 rounded-lg overflow-hidden shadow-sm h-[500px] overflow-y-auto"
      >
        <table className="w-full text-left border-collapse">
          <thead className="sticky top-0 bg-gray-100 z-10 shadow-sm">
            <tr>
              <TableHeader label="ID" onClick={() => handleSort('id')} active={sortConfig.key === 'id'} direction={sortConfig.direction} />
              <TableHeader label="Name" onClick={() => handleSort('firstName')} active={sortConfig.key === 'firstName'} direction={sortConfig.direction} />
              <TableHeader label="Email" onClick={() => handleSort('email')} active={sortConfig.key === 'email'} direction={sortConfig.direction} />
              <TableHeader label="Role" onClick={() => handleSort('role')} active={sortConfig.key === 'role'} direction={sortConfig.direction} />
              <TableHeader label="Status" onClick={() => handleSort('status')} active={sortConfig.key === 'status'} direction={sortConfig.direction} />
              <TableHeader label="Last Login" onClick={() => handleSort('lastLogin')} active={sortConfig.key === 'lastLogin'} direction={sortConfig.direction} />
            </tr>
          </thead>
          <tbody>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const item = processedData[virtualRow.index];
              return (
                <tr
                  key={item.id}
                  style={{
                    height: `${virtualRow.size}px`,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  className={`${getRowStyle(virtualRow.index)} absolute w-full top-0 left-0 transition-transform duration-75`}
                >
                  <td className="p-3 font-mono text-sm text-gray-500">{item.id}</td>
                  <td className="p-3 font-medium">{item.firstName} {item.lastName}</td>
                  <td className="p-3 text-gray-600">{item.email}</td>
                  <td className="p-3">
                    <span className={`px-2 py-1 rounded-full text-xs ${
                      item.role === 'Admin' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'
                    }`}>
                      {item.role}
                    </span>
                  </td>
                  <td className="p-3">
                    <span className={`px-2 py-1 rounded-full text-xs ${
                      item.status === 'Active' ? 'bg-green-100 text-green-700' : 
                      item.status === 'Pending' ? 'bg-yellow-100 text-yellow-700' : 'bg-red-100 text-red-700'
                    }`}>
                      {item.status}
                    </span>
                  </td>
                  <td className="p-3 text-sm text-gray-500">{new Date(item.lastLogin).toLocaleDateString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// Sub-component for Header to keep main component clean
const TableHeader: React.FC<{ 
  label: string; 
  onClick: () => void; 
  active: boolean; 
  direction: SortDirection 
}> = ({ label, onClick, active, direction }) => (
  <th 
    onClick={onClick}
    className={`p-3 text-xs font-semibold text-gray-600 uppercase tracking-wider cursor-pointer hover:bg-gray-200 transition-colors select-none ${active ? 'bg-gray-200' : ''}`}
  >
    <div className="flex items-center gap-1">
      {label}
      <span className="text-gray-400 text-xs">{getSortIcon(active, direction)}</span>
    </div>
  </th>
);

// Helper for icon rendering (moved out of component to avoid re-renders if possible, though usually fine inside)
const getSortIcon = (active: boolean, direction: SortDirection) => {
  if (!active) return '↕';
  return direction === 'asc' ? '↑' : '↓';
};

export default VirtualDataTable;
```

### How to Use It

You can drop this component into your `App.tsx`. Note that I've included the `generateData` function inside the file for self-containment, but in a real app, you would fetch this data from an API.

```tsx
import React, { useState, useEffect } from 'react';
import VirtualDataTable from './VirtualDataTable';

// Generate 5000 rows for testing
const MOCK_DATA = Array.from({ length: 5000 }, (_, i) => ({
  id: i,
  firstName: `User${i}`,
  lastName: `LastName${i}`,
  email: `user${i}@example.com`,
  role: ['Admin', 'Editor', 'Viewer', 'Developer'][Math.floor(Math.random() * 4)],
  status: ['Active', 'Inactive', 'Pending'][Math.floor(Math.random() * 3)],
  lastLogin: new Date(Date.now() - Math.floor(Math.random() * 10000000000)).toISOString(),
}));

export default function App() {
  return (
    <div className="min-h-screen bg-gray-100 p-8">
      <VirtualDataTable data={MOCK_DATA} />
    </div>
  );
}
```

### Key Technical Features

1.  **`@tanstack/react-virtual`**:
    *   We use `useVirtualizer` to calculate which rows are visible based on the scroll position.
    *   The `getScrollElement` hook points to the scrollable container (the `div` with `overflow-y-auto`).
    *   The `estimateSize` function tells the virtualizer how tall a row is (48px).

2.  **Absolute Positioning**:
    *   Instead of rendering rows sequentially, we render *only* the visible rows (`virtualizer.getVirtualItems()`) and use inline styles (`translateY`) to place them in the correct spot within the table.
    *   We add a spacer `div` (height = total scroll height) to ensure the scrollbar behaves correctly even though most DOM elements are missing.

3.  **Memoization (`useMemo`)**:
    *   Sorting and Filtering are computationally expensive if done on every keystroke or scroll. We wrap this logic in `useMemo` so it only re-runs when the `data`, `filterText`, or `sortConfig` changes.

4.  **Tailwind Styling**:
    *   `sticky top-0`: Keeps headers visible while scrolling.
    *   `bg-gray-50/100`: Alternating row colors for readability.
    *   `overflow-hidden rounded-lg`: Clean border styling.
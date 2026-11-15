import { useEffect, useState } from 'react';

export default function App() {
  const [status, setStatus] = useState('Checking...');
  useEffect(() => {
    fetch('/api/health')
      .then(response => { if (!response.ok) throw new Error('API error'); return response.json(); })
      .then(data => setStatus(data.status === 'ok' ? 'Connected' : 'Unavailable'))
      .catch(() => setStatus('Unavailable'));
  }, []);
  return <main><h1>Security Incident Management</h1><p>Application foundation</p><p>API status: <strong>{status}</strong></p></main>;
}

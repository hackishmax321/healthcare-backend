// pages/Dashboard.js
import React, { useEffect, useState } from 'react';
import Sidebar from '../components/Sidebar';
import Topbar from '../components/Topbar';
import Footer from '../components/Footer';
import PatientManagement from '../components/PatientsManagement';
import PatientSummary from '../components/PatientSummary';

const PatientsSection = () => {
  const [user, setUser] = useState(() => {
    const storedUser = localStorage.getItem('user');
    return storedUser ? JSON.parse(storedUser) : {};
  });
  const [selectedUser, setSelectedUser] = useState(null); 

  console.log(localStorage.getItem('user'))

  useEffect(() => {
    if (!user.username) {
      console.warn("User data is not available or invalid.");
    }
  }, [user]);
  return (
    <div className="container">
      <Sidebar />
      <div className="main">
        <Topbar />
        <div className="details">
          <PatientManagement setSelectedUser={setSelectedUser}/>
          <PatientSummary selectedUser={selectedUser}/>
        </div>
        <Footer />
      </div>
    </div>
  );
};

export default PatientsSection;

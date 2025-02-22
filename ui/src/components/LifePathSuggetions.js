import React, { useState, useEffect } from 'react';
import axios from 'axios';
import ENV from '../data/Env';
import { useOutletContext } from 'react-router-dom';

const LifePathSuggestions = () => {
  const { username } = useOutletContext();
  const [healthData, setHealthData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tip, setTip] = useState('');

  const healthTips = {
    general: [
      "Stay hydrated and drink at least 8 glasses of water daily.",
      "Aim for at least 30 minutes of moderate exercise per day.",
      "Practice deep breathing and mindfulness to reduce stress."
    ],
    diabetes: [
      "Maintain a balanced diet with low sugar intake.",
      "Monitor blood sugar levels regularly and stay active.",
      "Choose whole grains over processed foods for better glucose control."
    ],
    heart_patient: [
      "Engage in low-impact exercises like walking or yoga.",
      "Reduce salt intake to keep blood pressure in check.",
      "Avoid trans fats and eat more heart-friendly foods like fish and nuts."
    ],
    seniors: [
      "Prioritize bone health with calcium-rich foods.",
      "Stay socially active to maintain mental well-being.",
      "Ensure regular medical checkups for early detection of health issues."
    ]
  };

  useEffect(() => {
    const fetchHealthData = async () => {
      try {
        const response = await axios.get(`${ENV.SERVER}/users/${username}/all`);
        setHealthData(response.data);
        generateTip(response.data);
      } catch (err) {
        console.error('Error fetching health data:', err);
        setError('Failed to load health data');
      } finally {
        setLoading(false);
      }
    };

    fetchHealthData();
  }, [username]);

  const generateTip = (data) => {
    let tips = [...healthTips.general];
    
    if (data.diabetes) tips = tips.concat(healthTips.diabetes);
    if (data.heart_attack || data.heart_diseases) tips = tips.concat(healthTips.heart_patient);
    if (data.age > 50) tips = tips.concat(healthTips.seniors);
    
    setTip(tips[Math.floor(Math.random() * tips.length)]);
  };

  if (loading) {
    return <div>Loading...</div>;
  }

  if (error) {
    return <div>{error}</div>;
  }

  return (
    <div className="recentOrders">
      <h2>LifePath Suggestions</h2>
      <div className="tip-box" style={{
        padding: '15px',
        border: '2px solid #28a745',
        borderRadius: '10px',
        backgroundColor: '#f9f9f9',
        boxShadow: '0px 4px 6px rgba(0, 0, 0, 0.1)'
      }}>
        <p>{tip}</p>
      </div>
    </div>
  );
};

export default LifePathSuggestions;

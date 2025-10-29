// Configuration des zones de livraison pour OpenStreetMap/Leaflet
// Modifiez ces coordonnées selon vos besoins réels

const DELIVERY_ZONES_CONFIG = {
  // Zones de Paris (exemple)
  'paris-1': { 
    name: 'Paris 1er Arrondissement', 
    bounds: [[48.8580, 2.3300], [48.8667, 2.3522]], // Format Leaflet: [[sud, ouest], [nord, est]]
    color: '#3b82f6',
    deliveryFee: 2.50,
    estimatedTime: '25-35 min'
  },
  'paris-2': { 
    name: 'Paris 2ème Arrondissement', 
    bounds: [[48.8600, 2.3400], [48.8700, 2.3600]],
    color: '#10b981',
    deliveryFee: 2.50,
    estimatedTime: '20-30 min'
  },
  'paris-3': { 
    name: 'Paris 3ème Arrondissement', 
    bounds: [[48.8600, 2.3500], [48.8700, 2.3700]],
    color: '#f59e0b',
    deliveryFee: 3.00,
    estimatedTime: '25-35 min'
  },
  'paris-4': { 
    name: 'Paris 4ème Arrondissement', 
    bounds: [[48.8500, 2.3400], [48.8600, 2.3600]],
    color: '#ef4444',
    deliveryFee: 3.00,
    estimatedTime: '30-40 min'
  },
  
  // Zones d'autres villes (exemples)
  'lyon-centre': {
    name: 'Lyon Centre',
    bounds: [[45.7500, 4.8200], [45.7700, 4.8500]],
    color: '#8b5cf6',
    deliveryFee: 3.50,
    estimatedTime: '35-45 min'
  },
  
  'marseille-centre': {
    name: 'Marseille Centre',
    bounds: [[43.2800, 5.3700], [43.3000, 5.4000]],
    color: '#06b6d4',
    deliveryFee: 4.00,
    estimatedTime: '40-50 min'
  },
  
  'toulouse-centre': {
    name: 'Toulouse Centre',
    bounds: [[43.5900, 1.4200], [43.6200, 1.4600]],
    color: '#f97316',
    deliveryFee: 3.00,
    estimatedTime: '30-40 min'
  }
};

// Fonction pour obtenir les informations d'une zone
function getZoneInfo(zoneId) {
  return DELIVERY_ZONES_CONFIG[zoneId] || null;
}

// Fonction pour vérifier si une localisation est dans une zone (format Leaflet)
function isLocationInZone(lat, lng, zoneId) {
  const zone = DELIVERY_ZONES_CONFIG[zoneId];
  if (!zone) return false;
  
  const bounds = zone.bounds;
  // Format Leaflet: bounds[0] = [sud, ouest], bounds[1] = [nord, est]
  return lat >= bounds[0][0] && lat <= bounds[1][0] && 
         lng >= bounds[0][1] && lng <= bounds[1][1];
}

// Fonction pour trouver la zone d'une localisation
function findZoneForLocation(lat, lng) {
  for (const zoneId in DELIVERY_ZONES_CONFIG) {
    if (isLocationInZone(lat, lng, zoneId)) {
      return zoneId;
    }
  }
  return null;
}

// Fonction pour obtenir toutes les zones
function getAllZones() {
  return DELIVERY_ZONES_CONFIG;
}

// Fonction pour créer un rectangle Leaflet pour une zone
function createLeafletRectangle(zoneId) {
  const zone = DELIVERY_ZONES_CONFIG[zoneId];
  if (!zone) return null;
  
  return L.rectangle(zone.bounds, {
    color: zone.color,
    weight: 2,
    opacity: 0.8,
    fillColor: zone.color,
    fillOpacity: 0.1
  });
}

// Fonction pour créer un label Leaflet pour une zone
function createLeafletLabel(zoneId) {
  const zone = DELIVERY_ZONES_CONFIG[zoneId];
  if (!zone) return null;
  
  const center = [
    (zone.bounds[0][0] + zone.bounds[1][0]) / 2,
    (zone.bounds[0][1] + zone.bounds[1][1]) / 2
  ];
  
  return L.marker(center, {
    icon: L.divIcon({
      className: 'zone-label',
      html: `<div class="zone-label-text">${zone.name}</div>`,
      iconSize: [100, 20],
      iconAnchor: [50, 10]
    })
  });
}

// Export pour utilisation dans d'autres fichiers
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DELIVERY_ZONES_CONFIG,
    getZoneInfo,
    isLocationInZone,
    findZoneForLocation,
    getAllZones,
    createLeafletRectangle,
    createLeafletLabel
  };
}

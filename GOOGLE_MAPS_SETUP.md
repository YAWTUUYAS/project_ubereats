# Configuration Google Maps pour UberEats POC

## 🗺️ Fonctionnalités ajoutées

- **Autocomplétion d'adresse** : Recherche intelligente d'adresses françaises
- **Sélection sur carte** : Clic direct sur la carte pour choisir l'adresse
- **Géolocalisation** : Utilisation de la position actuelle de l'utilisateur
- **Zones de livraison** : Détection automatique de la zone de livraison
- **Validation en temps réel** : Vérification de la disponibilité de livraison
- **Interface responsive** : Adaptation mobile et desktop

## 🔧 Configuration requise

### 1. Obtenir une clé API Google Maps

1. Allez sur [Google Cloud Console](https://console.cloud.google.com/)
2. Créez un nouveau projet ou sélectionnez un projet existant
3. Activez les APIs suivantes :
   - **Maps JavaScript API**
   - **Places API**
   - **Geocoding API**
4. Créez des identifiants (clé API)
5. Configurez les restrictions de sécurité :
   - **Restrictions d'application** : Sites web HTTP
   - **Restrictions d'API** : Sélectionnez les APIs activées

### 2. Mettre à jour la clé API

Dans le fichier `frontend/templates/client/cart.html`, remplacez :

```html
<script async defer 
  src="https://maps.googleapis.com/maps/api/js?key=YOUR_API_KEY&libraries=places&callback=initMap">
</script>
```

Par votre vraie clé API :

```html
<script async defer 
  src="https://maps.googleapis.com/maps/api/js?key=VOTRE_CLE_API&libraries=places&callback=initMap">
</script>
```

### 3. Configuration des zones de livraison

Modifiez le fichier `frontend/static/js/delivery-zones.js` pour adapter les zones à votre région :

```javascript
const DELIVERY_ZONES_CONFIG = {
  'votre-zone-1': { 
    name: 'Nom de votre zone', 
    bounds: { 
      north: latitude_max, 
      south: latitude_min, 
      east: longitude_max, 
      west: longitude_min 
    },
    color: '#couleur_hex',
    deliveryFee: 2.50,
    estimatedTime: '25-35 min'
  },
  // ... autres zones
};
```

## 🎯 Utilisation

### Pour les clients

1. **Recherche d'adresse** : Tapez dans le champ de recherche
2. **Sélection sur carte** : Cliquez directement sur la carte
3. **Géolocalisation** : Utilisez le bouton "Utiliser ma position actuelle"
4. **Visualisation des zones** : Activez l'affichage des zones de livraison
5. **Validation** : La zone est détectée automatiquement

### Fonctionnalités disponibles

- ✅ **Autocomplétion** : Suggestions d'adresses en temps réel
- ✅ **Géocodage** : Conversion adresse ↔ coordonnées
- ✅ **Géolocalisation** : Position GPS de l'utilisateur
- ✅ **Zones de livraison** : Polygones colorés sur la carte
- ✅ **Validation** : Vérification de la disponibilité de livraison
- ✅ **Responsive** : Adaptation mobile et desktop

## 🔒 Sécurité

### Restrictions recommandées

1. **Restrictions d'application** :
   - Sites web HTTP : `https://votre-domaine.com/*`
   - Sites web HTTP : `http://localhost:*` (pour le développement)

2. **Restrictions d'API** :
   - Maps JavaScript API
   - Places API
   - Geocoding API

3. **Quotas** :
   - Configurez des quotas appropriés pour éviter les dépassements
   - Surveillez l'utilisation dans Google Cloud Console

## 🛠️ Personnalisation

### Styles de carte

Modifiez les styles dans la fonction `initMap()` :

```javascript
map = new google.maps.Map(document.getElementById('map'), {
  zoom: 13,
  center: defaultPosition,
  mapTypeId: 'roadmap',
  styles: [
    // Vos styles personnalisés
  ]
});
```

### Zones de livraison

Ajoutez/modifiez les zones dans `delivery-zones.js` :

```javascript
'nouvelle-zone': {
  name: 'Nouvelle Zone',
  bounds: { north: 48.8, south: 48.7, east: 2.4, west: 2.3 },
  color: '#ff6b6b',
  deliveryFee: 4.00,
  estimatedTime: '45-55 min'
}
```

### Couleurs et thème

Adaptez les couleurs dans `app.css` :

```css
:root {
  --primary: #votre-couleur-primaire;
  --success: #votre-couleur-succes;
  --danger: #votre-couleur-danger;
}
```

## 📱 Responsive Design

L'interface s'adapte automatiquement :

- **Desktop** : Carte et formulaire côte à côte
- **Mobile** : Carte au-dessus du formulaire
- **Tablette** : Adaptation fluide selon la taille d'écran

## 🐛 Dépannage

### Problèmes courants

1. **Carte ne s'affiche pas** :
   - Vérifiez la clé API
   - Vérifiez les restrictions de domaine
   - Consultez la console du navigateur

2. **Autocomplétion ne fonctionne pas** :
   - Vérifiez que Places API est activée
   - Vérifiez les restrictions de la clé API

3. **Géolocalisation refusée** :
   - Vérifiez les permissions du navigateur
   - Testez sur HTTPS en production

### Console de développement

Ouvrez la console du navigateur (F12) pour voir les erreurs potentielles.

## 💰 Coûts

### Tarification Google Maps

- **Maps JavaScript API** : 7$ pour 1000 chargements
- **Places API** : 17$ pour 1000 requêtes
- **Geocoding API** : 5$ pour 1000 requêtes

### Optimisations

- Mise en cache des résultats de géocodage
- Limitation des requêtes inutiles
- Utilisation de quotas appropriés

## 🚀 Déploiement

### Variables d'environnement

Pour la production, utilisez des variables d'environnement :

```javascript
const GOOGLE_MAPS_API_KEY = process.env.GOOGLE_MAPS_API_KEY || 'YOUR_API_KEY';
```

### HTTPS obligatoire

En production, HTTPS est requis pour :
- Géolocalisation
- Certaines APIs Google Maps
- Sécurité des données

## 📞 Support

Pour toute question ou problème :

1. Consultez la [documentation Google Maps](https://developers.google.com/maps/documentation)
2. Vérifiez les [forums de support](https://developers.google.com/maps/support)
3. Consultez les logs dans Google Cloud Console



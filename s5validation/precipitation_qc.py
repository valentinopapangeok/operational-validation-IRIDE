"""Reference-only neighbour review flags. Never certifies a sensor or drops pairs."""
import math
from statistics import median
DEFAULT_POLICY={'radius_km':10.0,'site_separation_km':.1,'minimum_neighbours':3,
 'isolated_wet_mm':10.0,'dry_neighbour_mm':.2,'homogeneous_minimum_neighbours':5,
 'homogeneous_absolute_difference_mm':5.0,'homogeneous_factor':5.0,
 'radar_absolute_difference_mm':2.0,'radar_factor':3.0}

def distance_km(a,b):
 la,lb=math.radians(a['latitude']),math.radians(b['latitude']);dl=math.radians(a['longitude']-b['longitude'])
 return 6371*2*math.asin(min(1,math.sqrt(math.sin((la-lb)/2)**2+math.cos(la)*math.cos(lb)*math.sin(dl/2)**2)))

def review_reference(gauge,others,policy=None):
 """Only independent gauge metadata and amounts enter the review decision."""
 p=dict(DEFAULT_POLICY,**(policy or {}));near=[]
 for other in others:
  if other['station']==gauge['station']:continue
  if not math.isfinite(other['mm']) or other['mm']<0:continue
  d=distance_km(gauge,other)
  if p['site_separation_km']<=d<=p['radius_km']:near.append((d,other))
 unique=[]
 for d,other in sorted(near,key=lambda t:(t[0],t[1]['station'])):
  if all(distance_km(other,s)>=p['site_separation_km'] for _,s in unique):unique.append((d,other))
 values=[r['mm'] for d,r in unique];count=len(values);flags=[];med=median(values) if values else None
 if count>=p['minimum_neighbours'] and gauge['mm']>=p['isolated_wet_mm'] and all(v<p['dry_neighbour_mm'] for v in values):flags.append('isolated_wet_reference')
 if count>=p['homogeneous_minimum_neighbours']:
  ordered=sorted(values);q1=ordered[int((count-1)*.25)];q3=ordered[int((count-1)*.75)]
  ratio=(gauge['mm']+.2)/(med+.2)
  if q3-q1<=max(1.,.5*med) and abs(gauge['mm']-med)>=p['homogeneous_absolute_difference_mm'] and (ratio>=p['homogeneous_factor'] or ratio<=1/p['homogeneous_factor']):flags.append('homogeneous_neighbour_outlier')
 return {'flags':flags,'neighbour_count':count,'neighbour_median_mm':med,
  'neighbours':[{'station':r['station'],'name':r.get('name',r['station']),'mm':r['mm'],'distance_km':d} for d,r in unique],
  'review_status':'REVIEW_REQUIRED' if flags else 'NO_SPATIAL_FLAG' if count>=p['minimum_neighbours'] else 'INSUFFICIENT_NEIGHBOURS'}

def radar_discrepancy(gauge_mm,radar_mm,policy=None):
 if radar_mm is None:return None
 p=dict(DEFAULT_POLICY,**(policy or {}))
 if not math.isfinite(radar_mm) or radar_mm<0:return None
 return abs(gauge_mm-radar_mm)>=p['radar_absolute_difference_mm'] and max(gauge_mm,radar_mm)>=p['radar_factor']*max(min(gauge_mm,radar_mm),.2)

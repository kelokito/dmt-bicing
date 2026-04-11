Querie Ideas

Totes les mètriques on agreguem per zona s'haurien de normaliztar pel nombre de parades capacitat.

1. Top parades amb més bicis per intervals de temps 10-11 , 11-12
 - Agregar Només per interval o també per mes-any (ja que no hi ha mesos que tenim més de 2+)
 - Agregar les parades per barri
 - Possibles patrons trobats -> al matñi les bicis estan en les zones altes la gent les fa servir per baixar a treballar i durant les hores de feina es queden allà. Matí més bicis zones altes, migdia - tarda zones baixes. Possible trajectòria
 - estiu platja

2. Top parades amb més desús / desaprofutades (Bicis / Capacitat, o més docks aviable) 
 - Similar a 1. però mesura relativa
 - Mateixes possibles agregacions

3. Correlació Entre Població i numero de bicis 
 - Més població implica menys bicis a les parades (ja sigui en relatiu on en absolut)?
 - Relació constant en els mesos?
 - Agafar dades de timestamps on no es facin servir bicis, per veure si les bicis 'tornen' 

4. Correlació entre pobalció / income i capacitat de les parades
 - Zones (barris o altres agregacions) on hi ha més gent les parades tenen més capacitat / hi ha més parades
 - Agregar les capacitats de les diferents parades d'una zona.

5. Comparació de zones on hi ha més bicis electriques i quines mecàniques
- Zones de baix -> si fa pujada menys probable que agafis una mecànica
- Estaria bé saber el total de bicis de cada tipus per no esbiaixar els resultats i respondre en %.

6. Zones on es troben més bicis en reparació
- Les zones més insegures de bcn (no tenim dades però ho sabem) tenen més bicis en reparació
- Podem fer servir zones més pobres
- Mirar si mesos més càlids i ha més averies
- Si es compleix el punt anteriro mirar les hores 

7. Proabilitat que hi hagi 1 o més espais lliures en una parada concreta en una interval horari concret.
- Hauria de tenir en compte dia de la semana, mes, la qual cosa es complicada de modelar
- Lo més senzill és agafar la proporició de espais lliures per parada-temps-dia , sense tenir en cpmpte el mes

8. Evolució de la ocupació de les parades
- Segurament fumada per fer un minivideo tipus els del cambis anticiclons del temps, que comenci a les 9 del matí i vegis com van buidant / omplint les parades amb un heatmap de barcelona
- Podriem fer mitjana de timestamps per hora o ensenyar totes les observacions individuals de cada mes
- Realment no seria una querie a no ser q agreguem per timestamp

9. Estacions de bici que no tenen una parada de metro a prop son més fetes servir
- S'hauria d'afegir una taula que tingues parades de metro i punts
- 'A prop' seria un radi arbitrari
- https://opendata-ajuntament.barcelona.cat/data/es/dataset/transports/resource/e07dec0d-4aeb-40f3-b987-e1f35e088ce2

10. Definir algunes zones com universitats / hotels i fixarnos en l'ús per elles
- Com definim auqetses zones? Radis desde un punt (per ex FIB, hotel X), barris?






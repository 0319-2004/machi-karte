<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel
    xmlns:core="http://www.opengis.net/citygml/2.0"
    xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
    xmlns:uro="https://www.geospatial.jp/iur/uro/3.2"
    xmlns:gml="http://www.opengis.net/gml">

  <!-- 通常の棟（13104）: センチネルなし、LOD1 -->
  <core:cityObjectMember>
    <bldg:Building gml:id="bldg-1">
      <core:creationDate>2024-03-15</core:creationDate>
      <bldg:usage>411</bldg:usage>
      <bldg:measuredHeight uom="m">9.3</bldg:measuredHeight>
      <bldg:storeysAboveGround>3</bldg:storeysAboveGround>
      <bldg:lod1Solid/>
      <bldg:lod0RoofEdge>
        <gml:MultiSurface>
          <gml:surfaceMember>
            <gml:Polygon>
              <gml:exterior>
                <gml:LinearRing>
                  <gml:posList srsDimension="3">35.6900 139.7000 30 35.6900 139.7005 30 35.6905 139.7005 30 35.6905 139.7000 30 35.6900 139.7000 30</gml:posList>
                </gml:LinearRing>
              </gml:exterior>
            </gml:Polygon>
          </gml:surfaceMember>
        </gml:MultiSurface>
      </bldg:lod0RoofEdge>
      <uro:buildingIDAttribute>
        <uro:BuildingIDAttribute>
          <uro:buildingID>13104-bldg-1</uro:buildingID>
          <uro:prefecture>13</uro:prefecture>
          <uro:city>13104</uro:city>
        </uro:BuildingIDAttribute>
      </uro:buildingIDAttribute>
      <uro:buildingDetailAttribute>
        <uro:BuildingDetailAttribute>
          <uro:detailedUsage>1310</uro:detailedUsage>
          <uro:surveyYear>2021</uro:surveyYear>
          <uro:buildingRoofEdgeArea>123.4</uro:buildingRoofEdgeArea>
        </uro:BuildingDetailAttribute>
      </uro:buildingDetailAttribute>
    </bldg:Building>
  </core:cityObjectMember>

  <!-- 不明群の棟（13104）: 全センチネル、LOD2 -->
  <core:cityObjectMember>
    <bldg:Building gml:id="bldg-2">
      <core:creationDate>2024-03-15</core:creationDate>
      <bldg:usage>461</bldg:usage>
      <bldg:measuredHeight uom="m">-9999</bldg:measuredHeight>
      <bldg:storeysAboveGround>9999</bldg:storeysAboveGround>
      <bldg:lod2MultiSurface/>
      <bldg:lod0RoofEdge>
        <gml:MultiSurface>
          <gml:surfaceMember>
            <gml:Polygon>
              <gml:exterior>
                <gml:LinearRing>
                  <gml:posList srsDimension="3">35.6910 139.7010 30 35.6910 139.7015 30 35.6915 139.7015 30 35.6915 139.7010 30 35.6910 139.7010 30</gml:posList>
                </gml:LinearRing>
              </gml:exterior>
            </gml:Polygon>
          </gml:surfaceMember>
        </gml:MultiSurface>
      </bldg:lod0RoofEdge>
      <uro:buildingIDAttribute>
        <uro:BuildingIDAttribute>
          <uro:buildingID>13104-bldg-2</uro:buildingID>
        </uro:BuildingIDAttribute>
      </uro:buildingIDAttribute>
      <uro:buildingDetailAttribute>
        <uro:BuildingDetailAttribute>
          <uro:detailedUsage>0</uro:detailedUsage>
          <uro:surveyYear>0001</uro:surveyYear>
          <uro:buildingRoofEdgeArea>-9999</uro:buildingRoofEdgeArea>
        </uro:BuildingDetailAttribute>
      </uro:buildingDetailAttribute>
    </bldg:Building>
  </core:cityObjectMember>

  <!-- 隣接区の棟（13113）: city_code フィルタで除外されること -->
  <core:cityObjectMember>
    <bldg:Building gml:id="bldg-9">
      <core:creationDate>2024-03-15</core:creationDate>
      <bldg:usage>411</bldg:usage>
      <bldg:measuredHeight uom="m">12.0</bldg:measuredHeight>
      <bldg:storeysAboveGround>4</bldg:storeysAboveGround>
      <bldg:lod0RoofEdge>
        <gml:MultiSurface>
          <gml:surfaceMember>
            <gml:Polygon>
              <gml:exterior>
                <gml:LinearRing>
                  <gml:posList srsDimension="3">35.6600 139.6900 30 35.6600 139.6905 30 35.6605 139.6905 30 35.6605 139.6900 30 35.6600 139.6900 30</gml:posList>
                </gml:LinearRing>
              </gml:exterior>
            </gml:Polygon>
          </gml:surfaceMember>
        </gml:MultiSurface>
      </bldg:lod0RoofEdge>
      <uro:buildingIDAttribute>
        <uro:BuildingIDAttribute>
          <uro:buildingID>13113-bldg-9</uro:buildingID>
        </uro:BuildingIDAttribute>
      </uro:buildingIDAttribute>
      <uro:buildingDetailAttribute>
        <uro:BuildingDetailAttribute>
          <uro:detailedUsage>1310</uro:detailedUsage>
          <uro:surveyYear>2021</uro:surveyYear>
          <uro:buildingRoofEdgeArea>200.0</uro:buildingRoofEdgeArea>
        </uro:BuildingDetailAttribute>
      </uro:buildingDetailAttribute>
    </bldg:Building>
  </core:cityObjectMember>

</core:CityModel>

# 3D modelling agent: headless FreeCAD (freecadcmd) and Blender (blender -b).
FROM agent-sandbox/agent-base:local

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends freecad blender python3-numpy \
 && rm -rf /var/lib/apt/lists/*
USER agent
WORKDIR /models

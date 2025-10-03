#!/bin/bash
PLAYBOOK_DIR="$(dirname "$0")"

# Launch Block2 playbook in fully detached background
nohup ansible-playbook -i "$PLAYBOOK_DIR/inventory/hosts-test.ini" \
      "$PLAYBOOK_DIR/block2_r2lab.yml" -c local > "$PLAYBOOK_DIR/block2.log" 2>&1 < /dev/null &


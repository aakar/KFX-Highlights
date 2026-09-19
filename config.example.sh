# Copy to config.sh and edit. config.sh is gitignored, so your paths and
# email address stay out of the repository.
#
#   cp config.example.sh config.sh
#
# Every setting has a working default — an empty config.sh is fine. The ":="
# form below means an environment variable of the same name wins, so you can
# override any of these for a single run:
#
#   KFX_EMAIL="" ./extract-kindle-highlights.sh

# Where to mount the Kindle. Created if it doesn't exist.
: "${KFX_MOUNT_POINT:=$HOME/mnt/kindle}"

# Where highlights are emailed. Readwise's address is the default.
# Set to "" to skip email entirely and just write the HTML files.
: "${KFX_EMAIL:=add@readwise.io}"

# Where generated HTML is kept. $WORK_DIR is the repo directory.
: "${KFX_OUTPUT_DIR:=$WORK_DIR/highlights}"

# Space-separated ASINs to skip: purchased books whose .kfx is DRM-locked
# can't be decoded, so they fail every run until listed here. The ASIN is the
# trailing part of the filename, e.g. "Some Title_B0BSYF3447.kfx" -> B0BSYF3447
: "${KFX_DRM_SKIP:=}"

# Only set this if the book folder isn't found automatically. It's the folder
# containing the .sdr sidecar folders, e.g.
#   $KFX_MOUNT_POINT/Internal Storage/documents/Downloads/Items01
# : "${KFX_KINDLE_DIR:=}"

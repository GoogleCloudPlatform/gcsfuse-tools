default_bs="512KB"
default_file_size="1GB"
default_iodepth=1
default_iotype="randread"
default_threads=1
default_nrfiles=1

default_go_version="1.24.0"
default_fio_version="3.39"
default_gcsfuse_version="enable_default_o_direct"


max_ssh_retries=5
retry_delay=30
poll_interval=60
timeout=180000


default_machine_type="c4-standard-96"
default_image_family="debian-11"
default_image_project="debian-cloud"
default_disk_size="500GB"
default_startup_script="./resources/starter_script.sh"
default_delete_after_use=False
default_zone="us-west4-a"
default_region="us-west4"
default_enable_hns=False
default_project="gcs-fuse-test"



default_fio_job_template="./resources/jobfile.fio"
default_mount_config_file=".resources/mount_config.yml"
default_fio_jobcases_file="./resources/fio_job_cases.csv"


default_iterations=3